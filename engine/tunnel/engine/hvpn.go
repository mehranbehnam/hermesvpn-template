package engine

// Locked config file (.hvpn): only HermesVPN can make one (Ed25519 signature,
// private key stays on the server) and the content is encrypted (AES-256-GCM)
// so the customer does not see server, account or payload.
//
//   HVPN1\n<base64 nonce|ciphertext>.<base64 signature>\n

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"strings"
	"time"
)

const hvpnMagic = "HVPN1"

func IsHVPN(data []byte) bool { return strings.HasPrefix(strings.TrimSpace(string(data)), hvpnMagic) }

func OpenHVPN(data []byte) (Config, error) {
	var cfg Config
	s := strings.TrimSpace(string(data))
	if !strings.HasPrefix(s, hvpnMagic) {
		return cfg, errors.New("not a HermesVPN file")
	}
	body := strings.TrimSpace(strings.TrimPrefix(s, hvpnMagic))
	parts := strings.Split(body, ".")
	if len(parts) != 2 {
		return cfg, errors.New("damaged file")
	}
	blob, err1 := base64.StdEncoding.DecodeString(parts[0])
	sig, err2 := base64.StdEncoding.DecodeString(parts[1])
	if err1 != nil || err2 != nil {
		return cfg, errors.New("damaged file")
	}
	if !ed25519.Verify(ed25519.PublicKey(hvpnPub()), blob, sig) {
		return cfg, errors.New("this file was not made by HermesVPN")
	}
	blk, _ := aes.NewCipher(hvpnKey())
	g, _ := cipher.NewGCM(blk)
	if len(blob) < g.NonceSize() {
		return cfg, errors.New("damaged file")
	}
	plain, err := g.Open(nil, blob[:g.NonceSize()], blob[g.NonceSize():], []byte(hvpnMagic))
	if err != nil {
		return cfg, errors.New("damaged file")
	}
	if err := json.Unmarshal(plain, &cfg); err != nil {
		return cfg, err
	}
	if cfg.Expires > 0 && time.Now().Unix() > cfg.Expires {
		return cfg, errors.New("config expired")
	}
	return cfg, nil
}

// SealHVPN is used only by the server-side tool (needs the private key).
func SealHVPN(cfg Config, priv ed25519.PrivateKey) (string, error) {
	plain, err := json.Marshal(cfg)
	if err != nil {
		return "", err
	}
	blk, _ := aes.NewCipher(hvpnKey())
	g, _ := cipher.NewGCM(blk)
	nonce := make([]byte, g.NonceSize())
	rand.Read(nonce)
	blob := append(nonce, g.Seal(nil, nonce, plain, []byte(hvpnMagic))...)
	sig := ed25519.Sign(priv, blob)
	return hvpnMagic + "\n" + base64.StdEncoding.EncodeToString(blob) + "." +
		base64.StdEncoding.EncodeToString(sig) + "\n", nil
}
