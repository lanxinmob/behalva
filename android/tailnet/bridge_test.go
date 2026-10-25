package tailnet

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/pem"
	"io"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"tailscale.com/logpolicy"
)

func TestPrivateLogDirectory(t *testing.T) {
	t.Setenv("TS_LOGS_DIR", "")
	directory := t.TempDir()
	if err := prepareLogDirectory(directory); err != nil {
		t.Fatal(err)
	}
	// Exercise the upstream function that panicked on the physical Android phone.
	actual := logpolicy.LogsDir(func(string, ...any) {})
	if actual != filepath.Join(directory, "logs") {
		t.Fatalf("log state escaped app directory: %q", actual)
	}
	probe := filepath.Join(actual, "write-test")
	if err := os.WriteFile(probe, []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	bad := filepath.Join(directory, "not-a-directory")
	if err := os.WriteFile(bad, []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := prepareLogDirectory(bad); err == nil {
		t.Fatal("unusable private storage accepted")
	}
}

func TestLogDirectoryCannotSwitchWhileNodeExists(t *testing.T) {
	t.Setenv("TS_LOGS_DIR", "unchanged")
	for _, closing := range []bool{false, true} {
		func() {
			registry.Lock()
			if closing {
				registry.closing["old-node"] = true
			} else {
				registry.nodes["old-handle"] = &node{directory: "old-node"}
			}
			registry.Unlock()
			defer func() {
				registry.Lock()
				delete(registry.closing, "old-node")
				delete(registry.nodes, "old-handle")
				registry.Unlock()
			}()
			_, err := start(Command{Origin: "https://workspace.example", Directory: filepath.Join(t.TempDir(), "new-node"), Pin: hex.EncodeToString(make([]byte, 32))})
			if err == nil {
				t.Fatal("overlapping node accepted")
			}
			if os.Getenv("TS_LOGS_DIR") != "unchanged" {
				t.Fatal("overlapping node changed private log directory")
			}
		}()
	}
}

func fixture(t *testing.T, expired bool, handler http.HandlerFunc) (*httptest.Server, string, string) {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	until := time.Now().Add(time.Hour)
	if expired {
		until = time.Now().Add(-time.Minute)
	}
	cert := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "workspace.example"}, DNSNames: []string{"workspace.example"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: until, KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, IsCA: true, BasicConstraintsValid: true}
	der, err := x509.CreateCertificate(rand.Reader, cert, cert, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	s := httptest.NewUnstartedServer(handler)
	s.Config.ErrorLog = log.New(io.Discard, "", 0)
	s.TLS = &tls.Config{Certificates: []tls.Certificate{{Certificate: [][]byte{der}, PrivateKey: key}}}
	s.StartTLS()
	t.Cleanup(s.Close)
	hash := sha256.Sum256(der)
	return s, string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})), hex.EncodeToString(hash[:])
}

func clientFor(t *testing.T, server *httptest.Server, host, pin, roots string) *http.Client {
	t.Helper()
	client, err := httpClient(func(ctx context.Context, network, address string) (net.Conn, error) {
		if address != "workspace.example:443" {
			t.Errorf("unexpected private destination %q", address)
		}
		return (&net.Dialer{}).DialContext(ctx, network, server.Listener.Addr().String())
	}, host, pin, roots)
	if err != nil {
		t.Fatal(err)
	}
	return client
}

func TestCertificateAuthentication(t *testing.T) {
	s, roots, pin := fixture(t, false, func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, `{"ok":true}`) })
	_, otherRoots, _ := fixture(t, false, func(http.ResponseWriter, *http.Request) {})
	for _, test := range []struct {
		name, host, pin, roots string
		success                bool
	}{
		{"trusted", "workspace.example", "", roots, true},
		{"unknown-root", "workspace.example", "", otherRoots, false},
		{"wrong-host", "wrong.example", "", roots, false},
		{"exact-pin-at-ip", "100.64.0.1", pin, "", true},
		{"changed-pin", "workspace.example", hex.EncodeToString(make([]byte, 32)), "", false},
	} {
		t.Run(test.name, func(t *testing.T) {
			response, err := clientFor(t, s, test.host, test.pin, test.roots).Get("https://workspace.example/api/state")
			if response != nil {
				response.Body.Close()
			}
			if (err == nil) != test.success {
				t.Fatalf("success=%v, error=%v", test.success, err)
			}
		})
	}
	expired, expiredRoots, expiredPin := fixture(t, true, func(http.ResponseWriter, *http.Request) { t.Error("expired certificate accepted") })
	for _, pin := range []string{"", expiredPin} {
		if response, err := clientFor(t, expired, "workspace.example", pin, expiredRoots).Get("https://workspace.example/api/state"); err == nil {
			response.Body.Close()
			t.Fatal("expired certificate accepted")
		}
	}
}

func TestRedirectAndUnicodeResponse(t *testing.T) {
	var leaked atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { leaked.Add(1) }))
	defer target.Close()
	s, roots, _ := fixture(t, false, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer fixture-token" {
			t.Error("authorization missing")
		}
		w.Header().Set("Location", target.URL)
		w.WriteHeader(307)
		io.WriteString(w, `{"message":"资料🙂"}`)
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	n := &node{ctx: ctx, client: clientFor(t, s, "workspace.example", "", roots), origin: "https://workspace.example", status: Snapshot{State: "Running"}}
	result, err := n.request(Command{Method: "POST", Path: "messages", Token: "fixture-token", Body: `{"body":"你好🙂"}`})
	if err != nil {
		t.Fatal(err)
	}
	value := result.(map[string]any)
	if value["status"] != 307 || value["body"] != `{"message":"资料🙂"}` || leaked.Load() != 0 {
		t.Fatalf("redirect followed or response changed: %#v", value)
	}
}

func TestAmbiguousPostNotReplayed(t *testing.T) {
	var received atomic.Int32
	s, roots, _ := fixture(t, false, func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		received.Add(1)
		conn, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		conn.Close()
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	n := &node{ctx: ctx, client: clientFor(t, s, "workspace.example", "", roots), origin: "https://workspace.example", status: Snapshot{State: "Running"}}
	if _, err := n.request(Command{Method: "POST", Path: "browser/command", Body: `{"action":"click"}`}); err == nil {
		t.Fatal("lost receipt reported success")
	}
	if received.Load() != 1 {
		t.Fatalf("operation received %d times", received.Load())
	}
}

func TestStopCancelsInflightRequest(t *testing.T) {
	received := make(chan struct{})
	s, roots, _ := fixture(t, false, func(w http.ResponseWriter, r *http.Request) { close(received); <-r.Context().Done() })
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	n := &node{ctx: ctx, client: clientFor(t, s, "workspace.example", "", roots), origin: "https://workspace.example", status: Snapshot{State: "Running"}}
	finished := make(chan error, 1)
	go func() { _, err := n.request(Command{Method: "GET", Path: "state"}); finished <- err }()
	select {
	case <-received:
	case <-time.After(5 * time.Second):
		t.Fatal("request did not arrive")
	}
	cancel()
	select {
	case err := <-finished:
		if err == nil {
			t.Fatal("cancelled request succeeded")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("stop did not cancel request")
	}
}
