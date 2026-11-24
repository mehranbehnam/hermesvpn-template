package main

import (
	"fmt"
	"net"
	"time"
)

func portOpen(port int) bool {
	c, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), 300*time.Millisecond)
	if err != nil {
		return false
	}
	c.Close()
	return true
}

type proxyState struct {
	Enable   uint32
	Server   string
	Override string
	AutoURL  string
	HadAuto  bool
}

type ProcInfo struct {
	Name string `json:"name"`
	Path string `json:"path"`
}
