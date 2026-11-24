package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

const windowTitle = "HermesVPN Connect"

type App struct {
	ctx    context.Context
	prefs  *Prefs
	store  *Store
	core   *Core
	routes *RouteRules

	connectMu  sync.Mutex
	connecting bool
	pendingURL string
	lastErr    string
}

func NewApp() *App {
	p := NewPrefs()
	a := &App{prefs: p, store: NewStore(p), core: NewCore()}
	a.routes = &RouteRules{prefs: p}
	return a
}

func (a *App) startup(ctx context.Context) {
	a.ctx = ctx
	a.core.onExit = func(reason string) {
		a.emitStatus()
		runtime.EventsEmit(a.ctx, "core-exit", reason)
	}
	if a.prefs.GetString("device_id", "") == "" {
		a.prefs.Set("device_id", newUUID())
	}
	// 1.0.23: «ایران مستقیم» برای همه پیش‌فرض شد (سایت‌ها و اپ‌های ایرانی که IP خارجی را رد می‌کنند
	// با VPN باز نمی‌شدند). یک‌بار برای همه روشن می‌شود؛ بعدش کاربر می‌تواند در تنظیمات برگرداند.
	if !a.prefs.GetBool("route_bypass_default_v1", false) {
		a.prefs.SetMany(map[string]any{"route_mode": "bypass_iran", "route_bypass_default_v1": true})
	}
	registerProtocol()
	cleanupOldExe()
	go a.statsLoop()
	go func() { _ = extractCore() }()
	a.routes.Load()
	for _, arg := range os.Args[1:] {
		if IsActivationLink(arg) || hasScheme(arg) {
			a.pendingURL = arg
		}
	}
	if l := takePendingLink(); l != "" {
		a.pendingURL = l
	}
	go ensureLauncherTask()
	go a.updateLoop()
	go func() { // links handed over by a non-elevated launch while we run
		for range time.Tick(1500 * time.Millisecond) {
			if l := takePendingLink(); l != "" && a.ctx != nil {
				runtime.WindowUnminimise(a.ctx)
				runtime.WindowShow(a.ctx)
				runtime.EventsEmit(a.ctx, "deeplink", l)
			}
		}
	}()
}

func hasArg(x string) bool {
	for _, a := range os.Args[1:] {
		if a == x {
			return true
		}
	}
	return false
}

func pendingLinkPath() string { return filepath.Join(dataDir(), "pending_link.txt") }

func savePendingLink(args []string) {
	for _, a := range args {
		if IsActivationLink(a) {
			_ = os.WriteFile(pendingLinkPath(), []byte(a), 0o600)
		}
	}
}

func takePendingLink() string {
	b, err := os.ReadFile(pendingLinkPath())
	if err != nil {
		return ""
	}
	_ = os.Remove(pendingLinkPath())
	return strings.TrimSpace(string(b))
}

func (a *App) domReady(ctx context.Context) {
	a.applyScreenshot()
	if a.prefs.GetBool("auto_connect_on_start", false) {
		go func() {
			time.Sleep(800 * time.Millisecond)
			a.Connect()
		}()
	}
}

func (a *App) shutdown(ctx context.Context) { a.core.Stop(); stopTunnel() }

// onSecondInstance: a hermesvpn:// link opened while the app is running.
func (a *App) onSecondInstance(args []string) {
	runtime.WindowUnminimise(a.ctx)
	runtime.WindowShow(a.ctx)
	for _, arg := range args {
		if IsActivationLink(arg) {
			runtime.EventsEmit(a.ctx, "deeplink", arg)
		}
	}
}

func (a *App) statsLoop() {
	t := time.NewTicker(time.Second)
	n := 0
	for range t.C {
		if a.ctx == nil || !a.core.Running() {
			continue
		}
		d, u := a.core.Speed()
		runtime.EventsEmit(a.ctx, "speed", map[string]int64{"down": d, "up": u})
		n++
		if n%5 == 2 {
			go func() {
				ms := a.core.MeasureDelay()
				runtime.EventsEmit(a.ctx, "ping", ms)
			}()
		}
	}
}

// ---------- state ----------

type State struct {
	Prefs       map[string]any `json:"prefs"`
	Running     bool           `json:"running"`
	Connecting  bool           `json:"connecting"`
	StartedAt   int64          `json:"startedAt"`
	Selected    *Entry         `json:"selected"`
	Version     string         `json:"version"`
	Build       string         `json:"build"`
	Admin       bool           `json:"admin"`
	PendingLink string         `json:"pendingLink"`
	LastError   string         `json:"lastError"`
}

func (a *App) GetState() State {
	a.connectMu.Lock()
	connecting := a.connecting
	a.connectMu.Unlock()
	st := State{
		Prefs: a.prefs.Snapshot(), Running: a.core.Running(), Connecting: connecting,
		StartedAt: a.core.StartedAt(), Selected: a.store.Selected(),
		Version: AppVersionName, Build: AppBuildStamp, Admin: isAdmin(),
		PendingLink: a.pendingURL, LastError: a.lastErr,
	}
	a.pendingURL = ""
	return st
}

func (a *App) emitStatus() {
	if a.ctx != nil {
		runtime.EventsEmit(a.ctx, "status", a.GetState())
	}
}

func (a *App) SetPref(key string, value any) {
	switch key {
	case "auth_password", "configs_json", "subscriptions_json":
		return
	}
	a.prefs.Set(key, value)
	if key == "screenshot_protection" {
		a.applyScreenshot()
	}
}

func (a *App) applyScreenshot() {
	setScreenshotProtection(windowTitle, a.prefs.GetBool("screenshot_protection", false))
}

// ---------- connect (MainActivity.connect) ----------

type ConnectResult struct {
	OK     bool   `json:"ok"`
	Error  string `json:"error,omitempty"`  // string key for i18n
	Detail string `json:"detail,omitempty"` // raw detail
	GoTo   string `json:"goto,omitempty"`
}

func (a *App) Connect() ConnectResult {
	a.connectMu.Lock()
	if a.connecting {
		a.connectMu.Unlock()
		return ConnectResult{OK: false}
	}
	a.connecting = true
	a.connectMu.Unlock()
	a.emitStatus()
	defer func() {
		a.connectMu.Lock()
		a.connecting = false
		a.connectMu.Unlock()
		a.emitStatus()
	}()
	r := a.connect()
	if r.OK {
		a.lastErr = ""
		go func() { time.Sleep(5 * time.Second); kickUpdateCheck() }() // check updates through the fresh tunnel too
	} else {
		a.lastErr = r.Detail
	}
	return r
}

func (a *App) connect() ConnectResult {
	username := strings.TrimSpace(a.prefs.GetString("auth_username", ""))
	password := a.prefs.GetString("auth_password", "")
	subID := strings.TrimSpace(a.prefs.GetString("sub_id", ""))
	selected := a.store.Selected()

	if selected != nil && selected.Group == GroupLocal {
		return a.startWithEntry(*selected)
	}
	if username == "" && subID == "" {
		if selected != nil {
			return a.startWithEntry(*selected)
		}
		return ConnectResult{Error: "error_no_link", GoTo: "settings"}
	}
	// Fast connect: with a saved config, connect at once and refresh the account in the background
	// (through the tunnel). Before, every connect first called the API outside the VPN — from Iran that
	// took 10-20 s (and on a slow network the whole timeout) before the tunnel even started.
	if selected != nil {
		r := a.startWithEntry(*selected)
		if r.OK {
			go func() {
				time.Sleep(4 * time.Second)
				var bg AccountResult
				if username != "" {
					bg = apiLogin(username, password)
				} else {
					bg = apiVless(subID)
				}
				if bg.OK {
					if username != "" {
						a.prefs.SetMany(map[string]any{"account_expiry": bg.ExpiryTime, "account_total_gb": bg.TotalGB})
					}
					a.store.SyncAccountLinks(bg.Links, "", false)
					a.prefs.Set("last_subscription_update", nowMs())
					a.emitStatus()
				}
			}()
			return r
		}
		// the saved config failed to start: fall through and fetch fresh links
	}
	var res AccountResult
	if username != "" {
		res = apiLogin(username, password)
	} else {
		res = apiVless(subID)
	}
	if !res.OK {
		key := map[string]string{
			"not_found": "error_not_found", "invalid_credentials": "error_invalid_credentials",
			"blocked": "error_blocked", "expired": "error_expired",
		}[res.Error]
		if key == "" {
			// network problem: fall back to the stored config, like a cached connect
			if selected != nil {
				return a.startWithEntry(*selected)
			}
			key = "error_network"
		}
		return ConnectResult{Error: key}
	}
	if username != "" {
		a.prefs.SetMany(map[string]any{"account_expiry": res.ExpiryTime, "account_total_gb": res.TotalGB})
	}
	a.store.SyncAccountLinks(res.Links, "", false)
	a.prefs.Set("last_subscription_update", nowMs())
	if sel := a.store.Selected(); sel != nil {
		return a.startWithEntry(*sel)
	}
	pref := a.prefs.GetString("preferred_server", "reality")
	uri := pickLink(res.Links, pref)
	if uri == "" {
		return ConnectResult{Error: "error_config"}
	}
	e := ParseEntry(uri, GroupHermes, "")
	return a.startWithEntry(*e)
}

func pickLink(links []VlessLink, preferred string) string {
	if len(links) == 0 {
		return ""
	}
	want := "Main"
	if preferred == "reality" {
		want = "Reality"
	}
	for _, l := range links {
		if strings.Contains(l.Label, want) {
			return l.URI
		}
	}
	return links[0].URI
}

func (a *App) buildOptions(mode string) BuildOptions {
	px, dr := a.routes.Lists()
	splitMode := a.prefs.GetString("tunnel_mode", "exclude")
	key := "excluded_apps"
	if splitMode == "include" {
		key = "included_apps"
	}
	dns := a.prefs.GetString("dns_server", "1.1.1.1")
	if net.ParseIP(strings.TrimSpace(dns)) == nil {
		dns = "1.1.1.1"
	}
	return BuildOptions{
		Mode: mode, Mux: a.prefs.GetBool("mux_enabled", false),
		BypassLAN: a.prefs.GetBool("bypass_lan", false), IPv6: a.prefs.GetBool("enable_ipv6", false),
		RouteMode: a.prefs.GetString("route_mode", "bypass_iran"), DNSServer: strings.TrimSpace(dns),
		SplitMode: splitMode, SplitApps: a.prefs.GetStrings(key), WfpLeak: true,
		ProxyDomain: px, DirectDom: dr,
	}
}

func (a *App) startWithEntry(e Entry) ConnectResult {
	a.routes.Load()
	mode := a.prefs.GetString("win_connection_mode", "tun")
	if mode == "tun" && !isAdmin() {
		mode = "proxy"
	}
	a.core.Stop()
	stopTunnel()
	killStaleCore(coreDir())
	choosePorts()
	opts := a.buildOptions(mode)
	if tid := tunnelIDOf(e); tid != "" {
		direct, terr := startTunnel(tid)
		if terr != nil {
			return ConnectResult{Error: "tunnel_connect_failed", Detail: terr.Error()}
		}
		opts.Tunnel, opts.TunnelDirect = true, direct
	}
	cfg, ips, err := BuildFromEntry(e, opts)
	if err != nil {
		return ConnectResult{Error: "error_config", Detail: err.Error()}
	}
	err = a.core.Start(cfg, mode)
	if err != nil && mode == "tun" {
		// Some PCs reject the WFP leak filter (other VPN/firewall). Retry without it, then as system proxy.
		opts.WfpLeak = false
		cfg, _, _ = BuildFromEntry(e, opts)
		if err2 := a.core.Start(cfg, mode); err2 == nil {
			err = nil
		} else {
			opts.Mode = "proxy"
			mode = "proxy"
			cfg, _, _ = BuildFromEntry(e, opts)
			if err3 := a.core.Start(cfg, mode); err3 == nil {
				err = nil
				runtime.EventsEmit(a.ctx, "toast", "win_tun_fallback")
			}
		}
	}
	if err != nil {
		return ConnectResult{Error: "error_config", Detail: err.Error()}
	}
	summary := fmt.Sprintf("%s · %s · DNS %s", strings.ToUpper(mode), e.Name, opts.DNSServer)
	if len(ips) > 0 {
		summary += " · " + strings.Join(ips, ", ")
	}
	if mode == "tun" {
		summary += " · 10.10.14.1/30"
		if opts.IPv6 {
			summary += " + IPv6"
		}
		if n := len(opts.SplitApps); n > 0 {
			summary += fmt.Sprintf(" · %s %d", opts.SplitMode, n)
		}
	}
	a.prefs.Set("last_tun_summary", summary)
	a.store.Select(e.ID)
	return ConnectResult{OK: true}
}

func (a *App) Disconnect() {
	a.core.Stop()
	stopTunnel()
	a.emitStatus()
}

func (a *App) LogTail() []string { return a.core.LogTail(200) }

// ---------- configs ----------

type ConfigsView struct {
	Entries    []Entry        `json:"entries"`
	SelectedID string         `json:"selectedId"`
	Subs       []Subscription `json:"subs"`
}

func (a *App) ListConfigs() ConfigsView {
	return ConfigsView{Entries: a.store.All(), SelectedID: a.store.SelectedID(), Subs: a.store.Subs()}
}

// SelectConfig: picking another config while connected switches the tunnel to it right away
// (no manual disconnect/connect), like v2rayNG. Not connected: it only becomes the selected one.
func (a *App) SelectConfig(id string) {
	prev := ""
	if s := a.store.Selected(); s != nil {
		prev = s.ID
	}
	a.store.Select(id)
	a.emitStatus()
	if id != prev && a.core.Running() {
		go func() {
			if s := a.store.Selected(); s != nil {
				a.startWithEntry(*s)
				a.emitStatus()
			}
		}()
	}
}
func (a *App) RenameConfig(id, name string) { a.store.Rename(id, strings.TrimSpace(name)) }
func (a *App) DeleteConfig(id string)       { a.store.Remove(id) }
func (a *App) ToggleFavorite(id string)     { a.store.ToggleFavorite(id) }
func (a *App) DeleteGroup(group string) {
	for _, e := range a.store.All() {
		if e.Group == group {
			a.store.Remove(e.ID)
		}
	}
	for _, s := range a.store.Subs() {
		if s.Name == group {
			a.store.RemoveSub(s.ID, false)
		}
	}
}

// PingEntries = PingTester: TCP connect time to each server, in parallel.
func (a *App) PingEntries(ids []string) map[string]int {
	want := map[string]bool{}
	for _, id := range ids {
		want[id] = true
	}
	res := map[string]int{}
	var mu sync.Mutex
	var wg sync.WaitGroup
	sem := make(chan struct{}, 24)
	for _, e := range a.store.All() {
		if len(ids) > 0 && !want[e.ID] {
			continue
		}
		wg.Add(1)
		go func(e Entry) {
			defer wg.Done()
			sem <- struct{}{}
			p := tcpPing(e)
			<-sem
			mu.Lock()
			res[e.ID] = p
			mu.Unlock()
		}(e)
	}
	wg.Wait()
	a.store.SetPings(res)
	return res
}

func entryHostPort(e Entry) (string, string) {
	if e.RawOutbound != "" {
		var o obj
		if json.Unmarshal([]byte(e.RawOutbound), &o) == nil {
			if s, ok := o["settings"].(obj); ok {
				for _, k := range []string{"vnext", "servers"} {
					if v, ok := s[k].(arr); ok && len(v) > 0 {
						if t, ok := v[0].(obj); ok {
							return fmt.Sprint(t["address"]), fmt.Sprint(t["port"])
						}
					}
				}
			}
		}
		return "", ""
	}
	if strings.HasPrefix(strings.ToLower(e.URI), "vmess://") {
		if v := decodeVmess(e.URI); v != nil {
			return fmt.Sprint(v["add"]), fmt.Sprint(v["port"])
		}
	}
	at := strings.Index(e.URI, "@")
	if at < 0 {
		return "", ""
	}
	hp := e.URI[at+1:]
	if i := strings.IndexAny(hp, "?#/"); i >= 0 {
		hp = hp[:i]
	}
	h, p, err := net.SplitHostPort(hp)
	if err != nil {
		return "", ""
	}
	return h, p
}

func tcpPing(e Entry) int {
	h, p := entryHostPort(e)
	if h == "" || p == "" {
		return PingTimeout
	}
	start := time.Now()
	c, err := net.DialTimeout("tcp", net.JoinHostPort(h, p), 3*time.Second)
	if err != nil {
		return PingTimeout
	}
	c.Close()
	return int(time.Since(start).Milliseconds())
}

// ImportText handles pasted links, multi-links, subscription URLs and activation links.
func (a *App) ImportText(text string) ImportResult {
	text = strings.TrimSpace(text)
	if IsActivationLink(text) {
		r := a.HandleActivationLink(text)
		if r.OK {
			return ImportResult{Added: r.Added}
		}
		return ImportResult{Error: r.Message}
	}
	if normalizeTunnel(text) != "" {
		return a.store.addTunnel(text, GroupLocal)
	}
	return a.store.Import(text, GroupLocal)
}

type JSONImport struct {
	OK    bool   `json:"ok"`
	Error string `json:"error,omitempty"`
}

func (a *App) ImportJSON(text string) JSONImport {
	o := ExtractPrimaryOutbound(text)
	if o == nil {
		return JSONImport{Error: "add_config_not_json"}
	}
	a.addOutbound(o)
	return JSONImport{OK: true}
}

func (a *App) addOutbound(o obj) {
	protocol, _ := o["protocol"].(string)
	if protocol == "" {
		protocol = "config"
	}
	address := protocol
	if s, ok := o["settings"].(obj); ok {
		for _, k := range []string{"vnext", "servers"} {
			if v, ok := s[k].(arr); ok && len(v) > 0 {
				if t, ok := v[0].(obj); ok {
					address = fmt.Sprint(t["address"])
				}
			}
		}
	}
	b, _ := json.Marshal(o)
	a.store.AddRawOutbound(fmt.Sprintf("%s (%s)", protocol, address), protocol, address, string(b), GroupLocal)
}

// ImportFileText = importV2boxFile: a full config first, otherwise share links.
func (a *App) ImportFileText(text string) ImportResult {
	if strings.TrimSpace(text) == "" {
		return ImportResult{Error: "invalid"}
	}
	if normalizeTunnel(text) != "" {
		return a.store.addTunnel(text, GroupLocal)
	}
	if o := ExtractPrimaryOutbound(text); o != nil {
		a.addOutbound(o)
		return ImportResult{Added: 1}
	}
	return a.store.Import(text, GroupLocal)
}

func (a *App) AddSubscription(name, u string) ImportResult {
	u = strings.TrimSpace(u)
	if u == "" {
		return ImportResult{Error: "empty"}
	}
	if strings.TrimSpace(name) == "" {
		name = "Subscription"
	}
	sub := a.store.AddSub(name, u, true)
	return a.store.ImportSubscription(sub)
}

func (a *App) RemoveSubscription(id string, alsoConfigs bool) { a.store.RemoveSub(id, alsoConfigs) }

type RefreshResult struct {
	AccountOK bool         `json:"accountOk"`
	Subs      ImportResult `json:"subs"`
}

// accountRefresh = AccountSync.refresh
func (a *App) accountRefresh() bool {
	username := strings.TrimSpace(a.prefs.GetString("auth_username", ""))
	password := a.prefs.GetString("auth_password", "")
	subID := strings.TrimSpace(a.prefs.GetString("sub_id", ""))
	if username == "" && subID == "" {
		return false
	}
	var r AccountResult
	if username != "" {
		r = apiLogin(username, password)
	} else {
		r = apiVless(subID)
	}
	if !r.OK || len(r.Links) == 0 {
		return false
	}
	a.store.SyncAccountLinks(r.Links, "", false)
	m := map[string]any{"last_subscription_update": nowMs()}
	if username != "" {
		m["account_expiry"] = r.ExpiryTime
		m["account_total_gb"] = r.TotalGB
	}
	a.prefs.SetMany(m)
	return true
}

func (a *App) RefreshAll() RefreshResult {
	ok := a.accountRefresh()
	return RefreshResult{AccountOK: ok, Subs: a.store.RefreshAllSubscriptions()}
}

func (a *App) AccountRefresh() bool { return a.accountRefresh() }

// ManualConfig builds a share link from the manual form (ConfigsActivity.showManualConfigDialog).
func (a *App) ManualConfig(protocol, remark, address, port, secret, method, user string) ImportResult {
	address, port, secret = strings.TrimSpace(address), strings.TrimSpace(port), strings.TrimSpace(secret)
	if address == "" || port == "" || secret == "" {
		return ImportResult{Error: "missing"}
	}
	r := strings.TrimSpace(remark)
	if r == "" {
		r = address
	}
	r = url.PathEscape(r)
	var uri string
	switch protocol {
	case "vless":
		uri = fmt.Sprintf("vless://%s@%s:%s?encryption=none&security=none#%s", secret, address, port, r)
	case "trojan":
		uri = fmt.Sprintf("trojan://%s@%s:%s#%s", url.PathEscape(secret), address, port, r)
	case "shadowsocks":
		m := strings.TrimSpace(method)
		if m == "" {
			m = "aes-256-gcm"
		}
		ui := base64.URLEncoding.EncodeToString([]byte(m + ":" + secret))
		uri = fmt.Sprintf("ss://%s@%s:%s#%s", ui, address, port, r)
	case "socks":
		if u := strings.TrimSpace(user); u != "" {
			ui := base64.URLEncoding.EncodeToString([]byte(u + ":" + secret))
			uri = fmt.Sprintf("socks://%s@%s:%s#%s", ui, address, port, r)
		} else {
			uri = fmt.Sprintf("socks://%s:%s#%s", address, port, r)
		}
	}
	return a.store.Import(uri, GroupLocal)
}

func (a *App) ClipboardText() string {
	s, _ := runtime.ClipboardGetText(a.ctx)
	return strings.TrimSpace(s)
}

func (a *App) CopyText(s string) bool { return runtime.ClipboardSetText(a.ctx, s) == nil }

// ---------- account ----------

type AuthResult struct {
	OK    bool   `json:"ok"`
	Error string `json:"error,omitempty"`
	Count int    `json:"count"`
}

func (a *App) persistSession(username, password string, r AccountResult) AuthResult {
	a.prefs.SetMany(map[string]any{
		"auth_username": username, "auth_password": password, "sub_id": "",
		"account_expiry": r.ExpiryTime, "account_total_gb": r.TotalGB,
		"last_subscription_update": nowMs(),
	})
	a.store.SyncAccountLinks(r.Links, "", false)
	return AuthResult{OK: true, Count: len(r.Links)}
}

func authErr(e string) string {
	switch e {
	case "invalid_credentials":
		return "error_invalid_credentials"
	case "blocked":
		return "error_blocked"
	case "expired":
		return "error_expired"
	case "not_found":
		return "error_register_not_found"
	case "username_taken":
		return "error_username_taken"
	}
	return "error_network"
}

func (a *App) Login(identifier, password string) AuthResult {
	identifier = strings.TrimPrefix(strings.TrimSpace(identifier), "@")
	if identifier == "" || password == "" {
		return AuthResult{Error: "error_fill_all_fields"}
	}
	r := apiLogin(identifier, password)
	if !r.OK {
		return AuthResult{Error: authErr(r.Error)}
	}
	return a.persistSession(identifier, password, r)
}

var emailRe = regexp.MustCompile(`^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$`)

func (a *App) Register(tg, email, newUser, password string) AuthResult {
	tg = strings.TrimPrefix(strings.TrimSpace(tg), "@")
	email, newUser = strings.TrimSpace(email), strings.TrimSpace(newUser)
	if tg == "" || email == "" || newUser == "" || password == "" {
		return AuthResult{Error: "error_fill_all_fields"}
	}
	if !emailRe.MatchString(email) {
		return AuthResult{Error: "error_bad_email"}
	}
	if len([]rune(password)) < 6 {
		return AuthResult{Error: "error_password_too_short"}
	}
	r := apiRegister(tg, email, newUser, password)
	if !r.OK {
		return AuthResult{Error: authErr(r.Error)}
	}
	return a.persistSession(newUser, password, r)
}

func (a *App) ForgotPassword(identifier string) bool {
	identifier = strings.TrimPrefix(strings.TrimSpace(identifier), "@")
	m := apiRaw("POST", "/app/forgot-password", map[string]string{"identifier": identifier}, 20*time.Second)
	ok, _ := m["ok"].(bool)
	return ok
}

func (a *App) Logout() {
	a.prefs.SetMany(map[string]any{"auth_username": "", "auth_password": "", "profile_avatar": nil})
}

func (a *App) creds() map[string]any {
	return map[string]any{
		"username": strings.TrimSpace(a.prefs.GetString("auth_username", "")),
		"password": a.prefs.GetString("auth_password", ""),
	}
}

func (a *App) ProfileFetch() map[string]any {
	m := apiRaw("POST", "/app/profile", a.creds(), 20*time.Second)
	if ok, _ := m["ok"].(bool); ok {
		if p, ok := m["profile"].(map[string]any); ok {
			if av, ok := p["avatar"].(string); ok && av != "" {
				a.prefs.Set("profile_avatar", av)
			}
		}
	}
	return m
}

func (a *App) ProfileUpdate(fields map[string]any) map[string]any {
	body := a.creds()
	for k, v := range fields {
		if k == "display_name" || k == "email" || k == "avatar" {
			body[k] = v
		}
	}
	if e, ok := fields["email"].(string); ok && e != "" && !emailRe.MatchString(e) {
		return map[string]any{"ok": false, "error": "error_bad_email"}
	}
	m := apiRaw("POST", "/app/profile/update", body, 20*time.Second)
	if ok, _ := m["ok"].(bool); ok {
		if av, ok := fields["avatar"].(string); ok {
			a.prefs.Set("profile_avatar", av)
		}
	}
	return m
}

func (a *App) ChangePassword(newPassword string) map[string]any {
	if len([]rune(newPassword)) < 6 {
		return map[string]any{"ok": false, "error": "error_password_too_short"}
	}
	body := a.creds()
	body["new_password"] = newPassword
	m := apiRaw("POST", "/app/change-password", body, 20*time.Second)
	if ok, _ := m["ok"].(bool); ok {
		a.prefs.Set("auth_password", newPassword)
	}
	return m
}

// SubscriptionInfoRefresh = SubscriptionInfoActivity.refresh
func (a *App) SubscriptionInfoRefresh() bool {
	u := strings.TrimSpace(a.prefs.GetString("auth_username", ""))
	if u == "" {
		return false
	}
	r := apiLogin(u, a.prefs.GetString("auth_password", ""))
	if !r.OK {
		return false
	}
	a.prefs.SetMany(map[string]any{"account_expiry": r.ExpiryTime, "account_total_gb": r.TotalGB})
	a.store.SyncAccountLinks(r.Links, "", false)
	return true
}

// SubscriptionSave = SubscriptionSettingsActivity.saveAndUpdate. Returns an i18n key (+count).
func (a *App) SubscriptionSave(raw string) map[string]any {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return map[string]any{"key": "add_config_invalid"}
	}
	if IsActivationLink(raw) {
		r := a.HandleActivationLink(raw)
		if r.OK {
			return map[string]any{"key": "subscription_updated"}
		}
		return map[string]any{"key": r.Message}
	}
	if isHTTPURL(raw) && !strings.Contains(raw, "/sub/") {
		r := a.store.Import(raw, GroupLocal)
		switch {
		case r.Added > 0:
			return map[string]any{"key": "add_config_added", "n": r.Added}
		case r.Error == "network":
			return map[string]any{"key": "error_network"}
		}
		return map[string]any{"key": "add_config_all_duplicates"}
	}
	id := raw
	if i := strings.Index(raw, "/sub/"); i >= 0 {
		id = strings.Trim(raw[i+5:], "/ ")
	}
	prev := a.prefs.GetString("sub_id", "")
	a.prefs.Set("sub_id", id)
	if a.accountRefresh() {
		return map[string]any{"key": "subscription_updated"}
	}
	a.prefs.Set("sub_id", prev) // not a HermesVPN account: keep the previous one
	return map[string]any{"key": "error_not_found"}
}

// ---------- purchase ----------

func (a *App) Plans() map[string]any { return apiRaw("GET", "/app/plans", nil, 15*time.Second) }

func (a *App) CreateOrder(planKey string) map[string]any {
	b := a.creds()
	b["plan_key"] = planKey
	return apiRaw("POST", "/app/orders", b, 15*time.Second)
}

func (a *App) ChooseMethod(orderID int, method string) map[string]any {
	b := a.creds()
	b["method"] = method
	return apiRaw("POST", fmt.Sprintf("/app/orders/%d/method", orderID), b, 15*time.Second)
}

func (a *App) UploadReceipt(orderID int, imageB64 string) map[string]any {
	b := a.creds()
	b["image_base64"] = imageB64
	return apiRaw("POST", fmt.Sprintf("/app/orders/%d/receipt", orderID), b, 30*time.Second)
}

func (a *App) OrderStatus(orderID int) map[string]any {
	c := a.creds()
	m := apiRaw("GET", fmt.Sprintf("/app/orders/%d?username=%s&password=%s", orderID,
		url.QueryEscape(fmt.Sprint(c["username"])), url.QueryEscape(fmt.Sprint(c["password"]))), nil, 15*time.Second)
	if st, _ := m["status"].(string); st == "approved" {
		go a.accountRefresh()
	}
	return m
}

// ---------- deep link (DeepLinkActivity) ----------

type LinkResult struct {
	OK      bool   `json:"ok"`
	Message string `json:"message,omitempty"` // i18n key on failure
	Account string `json:"account,omitempty"`
	Added   int    `json:"added"`
	Retry   bool   `json:"retry"`
}

func (a *App) HandleActivationLink(raw string) LinkResult {
	raw = strings.TrimSpace(raw)
	if strings.HasPrefix(strings.ToLower(raw), "vless://") {
		return a.importConfigs([]string{raw})
	}
	// hermesvpn://open#t=… → parse like the https form
	if strings.HasPrefix(strings.ToLower(raw), "hermesvpn://") {
		raw = "https://app.example.com/open/" + strings.TrimPrefix(raw[len("hermesvpn://"):], "open")
	}
	u, err := url.Parse(raw)
	if err != nil {
		return LinkResult{Message: "deeplink_no_token"}
	}
	if t := linkParam(u, "t"); t != "" {
		return a.redeem(t)
	}
	if t := linkParam(u, "token"); t != "" {
		return a.redeem(t)
	}
	if d := linkParam(u, "url"); d != "" {
		if dec, ok := decodePayload(d); ok {
			d = dec
		}
		return a.importConfigs(splitConfigs(d))
	}
	if c := linkParam(u, "c"); c != "" {
		dec, ok := decodePayload(c)
		if !ok {
			return LinkResult{Message: "deeplink_bad_payload"}
		}
		return a.importConfigs(splitConfigs(dec))
	}
	if s := linkParam(u, "s"); s != "" {
		subURL := s
		if dec, ok := decodePayload(s); ok && strings.HasPrefix(strings.ToLower(dec), "http") {
			subURL = dec
		}
		body, err := fetchText(subURL, "")
		if err != nil {
			return LinkResult{Message: "error_network", Retry: true}
		}
		text := body
		if dec, ok := decodePayload(strings.TrimSpace(body)); ok {
			text = dec
		}
		cfgs := splitConfigs(text)
		if len(cfgs) == 0 {
			cfgs = splitConfigs(body)
		}
		return a.importConfigs(cfgs)
	}
	return LinkResult{Message: "deeplink_no_token"}
}

func (a *App) redeem(token string) LinkResult {
	r := apiRedeem(token)
	if !r.OK {
		key := map[string]string{
			"invalid_token": "deeplink_invalid_token", "expired": "error_expired",
			"blocked": "error_blocked", "not_found": "error_not_found",
		}[r.Error]
		if key == "" {
			return LinkResult{Message: "error_network", Retry: true}
		}
		return LinkResult{Message: key}
	}
	a.store.SyncAccountLinks(r.Links, r.Account, true)
	if r.SubID != "" {
		a.prefs.Set("sub_id", r.SubID)
	}
	a.emitStatus()
	return LinkResult{OK: true, Account: r.Account, Added: len(r.Links)}
}

func (a *App) importConfigs(cfgs []string) LinkResult {
	if len(cfgs) == 0 {
		return LinkResult{Message: "deeplink_bad_payload"}
	}
	first := ""
	added := 0
	for _, u := range cfgs {
		e := ParseEntry(u, GroupHermes, "")
		if e == nil {
			continue
		}
		found := false
		for _, x := range a.store.All() {
			if x.URI == strings.TrimSpace(u) {
				if first == "" {
					first = x.ID
				}
				found = true
				break
			}
		}
		if found {
			continue
		}
		a.store.Add(*e)
		added++
		if first == "" {
			first = e.ID
		}
	}
	if first != "" {
		a.store.Select(first)
	}
	a.emitStatus()
	return LinkResult{OK: true, Added: added}
}

// ---------- speed test ----------

func (a *App) SpeedLatency() int {
	e := a.store.Selected()
	if e == nil {
		return PingTimeout
	}
	p := tcpPing(*e)
	a.store.SetPing(e.ID, p)
	return p
}

// SpeedDownload measures the device's current path (through the tunnel when connected).
func (a *App) SpeedDownload() float64 {
	cl := http.Client{Timeout: 25 * time.Second}
	start := time.Now()
	resp, err := cl.Get("https://speed.cloudflare.com/__down?bytes=10000000")
	if err != nil {
		return -1
	}
	defer resp.Body.Close()
	var total int64
	buf := make([]byte, 32*1024)
	for {
		n, err := resp.Body.Read(buf)
		total += int64(n)
		if err != nil || time.Since(start) > 10*time.Second {
			break
		}
	}
	sec := time.Since(start).Seconds()
	if sec <= 0 {
		return -1
	}
	return float64(total) * 8 / 1e6 / sec
}

// ---------- misc ----------

func (a *App) OpenURL(u string) { _ = openURL(u) }

func (a *App) Processes() []ProcInfo {
	l := listProcesses()
	sort.Slice(l, func(i, j int) bool { return strings.ToLower(l[i].Name) < strings.ToLower(l[j].Name) })
	return l
}

func (a *App) SetAutostart(on bool) bool {
	err := setAutostart(on)
	a.prefs.Set("win_autostart", on && err == nil)
	return err == nil
}

func (a *App) AutostartEnabled() bool { return autostartEnabled() }

func (a *App) CheckUpdate() map[string]any {
	a.prefs.Set("update_last_check", nowMs())
	info, err := fetchUpdate()
	if err != nil {
		return map[string]any{"ok": false}
	}
	if info == nil {
		return map[string]any{"ok": true, "available": false, "current": AppVersionName}
	}
	return map[string]any{"ok": true, "available": true, "info": info, "current": AppVersionName}
}

func (a *App) SkipUpdate(code int) { a.prefs.Set("update_skipped_code", code) }

// updateLoop: shortly after start and then every 30 minutes, look for an approved new
// version. When one appears: a native Windows notification (once per version) and an
// in-app banner. Clicking the notification brings the window to the front.
var updateKick = make(chan struct{}, 1)

// kickUpdateCheck makes the update loop check right now (called after a successful connect).
func kickUpdateCheck() {
	select {
	case updateKick <- struct{}{}:
	default:
	}
}

func (a *App) updateLoop() {
	time.Sleep(8 * time.Second)
	if a.ctx == nil {
		return
	}
	if runtime.InitializeNotifications(a.ctx) == nil {
		runtime.OnNotificationResponse(a.ctx, func(r runtime.NotificationResult) {
			runtime.WindowUnminimise(a.ctx)
			runtime.WindowShow(a.ctx)
			runtime.EventsEmit(a.ctx, "update-open")
		})
	}
	for {
		info, err := fetchUpdate()
		a.prefs.Set("update_last_check", nowMs())
		if err == nil && info != nil {
			runtime.EventsEmit(a.ctx, "update-available", info)
			if int(a.prefs.GetInt64("update_notified_code", 0)) != info.VersionCode {
				fa := a.prefs.GetString("app_language", "fa") != "en"
				title, body := "HermesVPN Connect "+info.VersionName, "A new version is ready — click to update."
				if fa {
					title = "نسخه‌ی جدید HermesVPN Connect آمد (" + info.VersionName + ")"
					body = "برای به‌روزرسانی کلیک کن — آخرین و سریع‌ترین نسخه."
					if strings.TrimSpace(info.Notes) != "" {
						body = info.Notes
					}
				}
				_ = runtime.SendNotification(a.ctx, runtime.NotificationOptions{
					ID: fmt.Sprintf("hermes-update-%d", info.VersionCode), Title: title, Body: body,
				})
				a.prefs.Set("update_notified_code", info.VersionCode)
			}
		}
		// owner: approved updates reach users within 15 min. A failed check (no network yet, filtered
		// route) retries after 2 min; a successful connect wakes the loop at once (updateKick).
		wait := 15 * time.Minute
		if err != nil {
			wait = 2 * time.Minute
		}
		select {
		case <-updateKick:
		case <-time.After(wait):
		}
	}
}

// InstallUpdate downloads the approved installer and runs it silently (/S). The installer
// closes this app, installs over it and starts the new version itself — no window, no
// questions. (The old code put the installer in place of the app exe; the installer then
// killed itself together with the app, which is why updates stopped half-way.)
func (a *App) InstallUpdate(u string) map[string]any {
	path, err := downloadUpdate(u, func(done, total int64) {
		runtime.EventsEmit(a.ctx, "update-progress", map[string]int64{"done": done, "total": total})
	})
	if err != nil {
		return map[string]any{"ok": false, "error": err.Error()}
	}
	a.core.Stop()
	cmd := exec.Command(path, "/S")
	if err := cmd.Start(); err != nil {
		return map[string]any{"ok": false, "error": err.Error()}
	}
	go func() {
		time.Sleep(500 * time.Millisecond)
		runtime.Quit(a.ctx)
	}()
	return map[string]any{"ok": true}
}

func copyFile(src, dst string) error {
	b, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	return os.WriteFile(dst, b, 0o755)
}

func cleanupOldExe() {
	if exe, err := os.Executable(); err == nil {
		_ = os.Remove(exe + ".old")
	}
	_ = os.Remove(filepath.Join(dataDir(), "HermesVPN-Connect-update.exe"))
	_ = os.Remove(filepath.Join(dataDir(), "HermesVPN-Setup-update.exe"))
}

func (a *App) Quit() {
	a.core.Stop()
	stopTunnel()
	runtime.Quit(a.ctx)
}
