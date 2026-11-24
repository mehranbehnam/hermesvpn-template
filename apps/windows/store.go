package main

import (
	"crypto/rand"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"
)

const (
	GroupHermes = "HermesVPN"
	GroupLocal  = "Local"
	PingUnknown = -1
	PingTimeout = -2
)

// Entry = ConfigStore.Entry on Android.
type Entry struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	URI         string `json:"uri"`
	Protocol    string `json:"protocol"`
	Address     string `json:"address"`
	Group       string `json:"group"`
	Ping        int    `json:"ping"`
	Favorite    bool   `json:"favorite"`
	RawOutbound string `json:"raw_outbound,omitempty"`
	Account     string `json:"account,omitempty"`
}

type Subscription struct {
	ID         string `json:"id"`
	Name       string `json:"name"`
	URL        string `json:"url"`
	AutoUpdate bool   `json:"auto_update"`
	LastUpdate int64  `json:"last_update"`
	Count      int    `json:"count"`
}

type VlessLink struct {
	Label string `json:"label"`
	URI   string `json:"uri"`
}

func newUUID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

func nowMs() int64 { return time.Now().UnixMilli() }

// Store keeps configs and subscriptions inside prefs, like Android.
type Store struct {
	mu    sync.Mutex
	prefs *Prefs
}

func NewStore(p *Prefs) *Store { return &Store{prefs: p} }

func (s *Store) loadEntries() []Entry {
	raw := s.prefs.GetString("configs_json", "")
	out := []Entry{}
	if raw != "" {
		_ = json.Unmarshal([]byte(raw), &out)
	}
	for i := range out {
		if out[i].Group == "" {
			out[i].Group = GroupLocal
		}
	}
	return out
}

func (s *Store) saveEntries(list []Entry) {
	b, _ := json.Marshal(list)
	s.prefs.Set("configs_json", string(b))
}

func (s *Store) All() []Entry {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.loadEntries()
}

func (s *Store) SelectedID() string { return s.prefs.GetString("selected_config_id", "") }

func (s *Store) Select(id string) { s.prefs.Set("selected_config_id", id) }

func (s *Store) Selected() *Entry {
	id := s.SelectedID()
	if id == "" {
		return nil
	}
	for _, e := range s.All() {
		if e.ID == id {
			e := e
			return &e
		}
	}
	return nil
}

// ParseEntry = ConfigStore.parse.
func ParseEntry(uri, group, fallbackName string) *Entry {
	t := strings.TrimSpace(uri)
	i := strings.Index(t, "://")
	if i <= 0 {
		return nil
	}
	protocol := strings.ToUpper(t[:i])
	host := ""
	if at := strings.Index(t, "@"); at >= 0 {
		hp := t[at+1:]
		if q := strings.IndexAny(hp, "?#/"); q >= 0 {
			hp = hp[:q]
		}
		if c := strings.LastIndex(hp, ":"); c > 0 {
			host = hp[:c]
		} else {
			host = hp
		}
	} else if protocol == "VMESS" {
		if v := decodeVmess(t); v != nil {
			host = fmt.Sprint(v["add"])
			if fallbackName == "" {
				if ps, ok := v["ps"].(string); ok {
					fallbackName = ps
				}
			}
		}
	}
	tag := ""
	if h := strings.Index(t, "#"); h >= 0 {
		tag = t[h+1:]
		if d, err := url.PathUnescape(tag); err == nil {
			tag = d
		}
	}
	name := tag
	if name == "" {
		name = fallbackName
	}
	if name == "" {
		name = host
	}
	if name == "" {
		name = protocol
	}
	return &Entry{ID: newUUID(), Name: name, URI: t, Protocol: protocol, Address: host, Group: group, Ping: PingUnknown}
}

func (s *Store) addLocked(list []Entry, e Entry) []Entry {
	for _, x := range list {
		if x.URI == e.URI {
			return list
		}
	}
	return append(list, e)
}

func (s *Store) Add(e Entry) bool {
	s.mu.Lock()
	list := s.loadEntries()
	n := len(list)
	list = s.addLocked(list, e)
	added := len(list) > n
	s.saveEntries(list)
	s.mu.Unlock()
	if added && s.SelectedID() == "" {
		s.Select(e.ID)
	}
	return added
}

func (s *Store) AddRawOutbound(name, protocol, address, outbound, group string) Entry {
	e := Entry{
		ID: newUUID(), Name: name, URI: fmt.Sprintf("json://%d", javaHash(outbound)),
		Protocol: strings.ToUpper(protocol), Address: address, Group: group, Ping: PingUnknown,
		RawOutbound: outbound,
	}
	s.Add(e)
	return e
}

func javaHash(s string) int32 {
	var h int32
	for _, r := range s {
		h = 31*h + int32(r)
	}
	return h
}

func (s *Store) Remove(id string) {
	os.Remove(tunnelSpecPath(id)) // emergency route file, if any
	s.mu.Lock()
	list := s.loadEntries()
	out := list[:0]
	for _, e := range list {
		if e.ID != id {
			out = append(out, e)
		}
	}
	s.saveEntries(out)
	s.mu.Unlock()
	if s.SelectedID() == id {
		s.prefs.Set("selected_config_id", nil)
		if len(out) > 0 {
			s.Select(out[0].ID)
		}
	}
}

func (s *Store) update(id string, f func(*Entry)) {
	s.mu.Lock()
	defer s.mu.Unlock()
	list := s.loadEntries()
	for i := range list {
		if list[i].ID == id {
			f(&list[i])
		}
	}
	s.saveEntries(list)
}

func (s *Store) Rename(id, name string)   { s.update(id, func(e *Entry) { e.Name = name }) }
func (s *Store) ToggleFavorite(id string) { s.update(id, func(e *Entry) { e.Favorite = !e.Favorite }) }
func (s *Store) SetPing(id string, p int) { s.update(id, func(e *Entry) { e.Ping = p }) }

func (s *Store) SetPings(m map[string]int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	list := s.loadEntries()
	for i := range list {
		if p, ok := m[list[i].ID]; ok {
			list[i].Ping = p
		}
	}
	s.saveEntries(list)
}

// SyncAccountLinks = ConfigStore.syncAccountLinks.
func (s *Store) SyncAccountLinks(links []VlessLink, account string, selectFirst bool) {
	if len(links) == 0 {
		return
	}
	s.mu.Lock()
	list := s.loadEntries()
	incoming := map[string]bool{}
	for _, l := range links {
		incoming[strings.TrimSpace(l.URI)] = true
	}
	kept := list[:0]
	for _, e := range list {
		if e.Group == GroupHermes && e.Account == account && !incoming[e.URI] {
			continue
		}
		kept = append(kept, e)
	}
	list = kept
	for _, l := range links {
		label := l.Label
		if account != "" {
			label = l.Label + " · " + account
		}
		uri := strings.TrimSpace(l.URI)
		found := false
		for i := range list {
			if list[i].URI == uri {
				list[i].Name = label
				list[i].Group = GroupHermes
				list[i].Account = account
				found = true
				break
			}
		}
		if found {
			continue
		}
		if e := ParseEntry(uri, GroupHermes, label); e != nil {
			e.Name = label
			e.Account = account
			list = append(list, *e)
		}
	}
	s.saveEntries(list)
	s.mu.Unlock()
	stored := s.All()
	sel := s.SelectedID()
	if selectFirst {
		for _, e := range stored {
			if e.Account == account && incoming[e.URI] {
				s.Select(e.ID)
				return
			}
		}
	} else {
		for _, e := range stored {
			if e.ID == sel {
				return
			}
		}
		for _, e := range stored {
			if e.Group == GroupHermes {
				s.Select(e.ID)
				return
			}
		}
	}
}

// ---- subscriptions ----

func (s *Store) Subs() []Subscription {
	raw := s.prefs.GetString("subscriptions_json", "")
	out := []Subscription{}
	if raw != "" {
		_ = json.Unmarshal([]byte(raw), &out)
	}
	return out
}

func (s *Store) saveSubs(l []Subscription) {
	b, _ := json.Marshal(l)
	s.prefs.Set("subscriptions_json", string(b))
}

func (s *Store) AddSub(name, url string, auto bool) Subscription {
	l := s.Subs()
	base := strings.TrimSpace(name)
	if base == "" {
		base = "Subscription"
	}
	n := base
	exists := func(x string) bool {
		for _, s := range l {
			if s.Name == x {
				return true
			}
		}
		return false
	}
	for k := 2; exists(n); k++ {
		n = fmt.Sprintf("%s-%d", base, k)
	}
	sub := Subscription{ID: newUUID(), Name: n, URL: url, AutoUpdate: auto}
	s.saveSubs(append(l, sub))
	return sub
}

func (s *Store) UpdateSub(sub Subscription) {
	l := s.Subs()
	for i := range l {
		if l[i].ID == sub.ID {
			l[i] = sub
			s.saveSubs(l)
			return
		}
	}
	s.saveSubs(append(l, sub))
}

func (s *Store) RemoveSub(id string, alsoConfigs bool) {
	l := s.Subs()
	var name string
	out := l[:0]
	for _, x := range l {
		if x.ID == id {
			name = x.Name
			continue
		}
		out = append(out, x)
	}
	s.saveSubs(out)
	if alsoConfigs && name != "" {
		for _, e := range s.All() {
			if e.Group == name {
				s.Remove(e.ID)
			}
		}
	}
}
