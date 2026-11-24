// hvpn-seal (server only): JSON config on stdin -> locked .hvpn on stdout.
package main

import (
	"crypto/ed25519"
	"encoding/json"
	"fmt"
	"os"

	"hermesvpn/tunnel/engine"
)

func main() {
	key := os.Getenv("HVPN_KEY")
	if key == "" {
		key = os.Getenv("HOME") + "/winapp/tunnel/.keys/hvpn_sign.key"
	}
	priv, err := os.ReadFile(key)
	if err != nil || len(priv) != ed25519.PrivateKeySize {
		fmt.Fprintln(os.Stderr, "signing key missing")
		os.Exit(1)
	}
	var c engine.Config
	if err := json.NewDecoder(os.Stdin).Decode(&c); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	out, err := engine.SealHVPN(c, priv)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Print(out)
}
