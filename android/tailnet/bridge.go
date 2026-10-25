// Package tailnet connects only this application's requests to a private workspace.
package tailnet

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"tailscale.com/envknob"
	"tailscale.com/net/netmon"
	"tailscale.com/tsnet"
)

const maximumBytes = 48 << 20

type Command struct {
	Operation        string      `json:"op"`
	Handle           string      `json:"handle"`
	Directory        string      `json:"directory"`
	Origin           string      `json:"origin"`
	Roots            string      `json:"roots"`
	Pin              string      `json:"pin"`
	Path             string      `json:"path"`
	Method           string      `json:"method"`
	Body             string      `json:"body"`
	Token            string      `json:"token"`
	Interfaces       []Interface `json:"interfaces"`
	DefaultInterface string      `json:"default_interface"`
}

type Interface struct {
	Name      string   `json:"name"`
	Index     int      `json:"index"`
	MTU       int      `json:"mtu"`
	Flags     uint     `json:"flags"`
	Addresses []string `json:"addresses"`
}

type Snapshot struct {
	State   string `json:"state"`
	AuthURL string `json:"auth_url,omitempty"`
	Error   string `json:"error,omitempty"`
}

type node struct {
	mu        sync.Mutex
	server    *tsnet.Server
	client    *http.Client
	ctx       context.Context
	cancel    context.CancelFunc
	origin    string
	directory string
	status    Snapshot
	started   chan struct{}
}

var registry = struct {
	sync.Mutex
	nodes   map[string]*node
	closing map[string]bool
}{nodes: make(map[string]*node), closing: make(map[string]bool)}

var interfaces = struct {
	sync.RWMutex
	values []netmon.Interface
}{}
var interfaceGetter sync.Once

func init() {
	envknob.SetNoLogsNoSupport()
}

// Call has no listening socket and returns no node keys or request credentials.
func Call(data []byte) (answer []byte) {
	defer func() {
		if recover() != nil {
			answer = []byte(`{"error":"Native transport failed"}`)
		}
	}()
	var cmd Command
	if len(data) > maximumBytes || json.Unmarshal(data, &cmd) != nil {
		return []byte(`{"error":"Invalid transport command"}`)
	}
	result, err := execute(cmd)
	if err != nil {
		result = map[string]any{"error": err.Error()}
	}
	answer, _ = json.Marshal(result)
	return answer
}

func execute(cmd Command) (any, error) {
	switch cmd.Operation {
	case "network":
		return map[string]bool{"ok": true}, updateInterfaces(cmd)
	case "start":
		return start(cmd)
	}
	registry.Lock()
	n := registry.nodes[cmd.Handle]
	registry.Unlock()
	if n == nil {
		return nil, errors.New("Connection instance is closed")
	}
	switch cmd.Operation {
	case "status":
		n.mu.Lock()
		defer n.mu.Unlock()
		return n.status, nil
	case "stop", "logout":
		registry.Lock()
		if registry.nodes[cmd.Handle] != n {
			registry.Unlock()
			return nil, errors.New("Connection instance is closed")
		}
		delete(registry.nodes, cmd.Handle)
		registry.closing[n.directory] = true
		registry.Unlock()
		n.cancel()
		// The Kotlin control worker is independent of requests and the main thread.
		// Wait for Start before Close so a late startup cannot resurrect this node.
		defer func() { registry.Lock(); delete(registry.closing, n.directory); registry.Unlock() }()
		<-n.started
		if err := n.server.Close(); err != nil {
			return nil, errors.New("Private connection shutdown failed")
		}
		if cmd.Operation == "logout" {
			if err := os.RemoveAll(n.directory); err != nil {
				return nil, errors.New("Could not clear the local node identity")
			}
		}
		return map[string]bool{"ok": true}, nil
	case "request":
		return n.request(cmd)
	default:
		return nil, errors.New("Unknown transport operation")
	}
}

func start(cmd Command) (any, error) {
	u, err := url.Parse(cmd.Origin)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return nil, errors.New("Workspace must be an HTTPS origin")
	}
	directory := filepath.Clean(cmd.Directory)
	if !filepath.IsAbs(directory) || filepath.Dir(directory) == directory {
		return nil, errors.New("Invalid node directory")
	}
	client, err := httpClient(nil, u.Hostname(), cmd.Pin, cmd.Roots)
	if err != nil {
		return nil, err
	}
	registry.Lock()
	defer registry.Unlock()
	// TS_LOGS_DIR is process-global. Match Kotlin's single-node lifecycle here
	// too, so no second node can redirect a starting node's private log state.
	if len(registry.nodes) != 0 || len(registry.closing) != 0 {
		return nil, errors.New("Previous connection must finish stopping before starting another")
	}
	if err := os.MkdirAll(directory, 0700); err != nil {
		return nil, errors.New("Cannot create private node state")
	}
	if err := os.Chmod(directory, 0700); err != nil {
		return nil, errors.New("Cannot protect private node state")
	}
	if err := prepareLogDirectory(directory); err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(context.Background())
	identity := sha256.Sum256([]byte(directory))
	server := &tsnet.Server{Dir: directory, Hostname: "personal-assistant-" + hex.EncodeToString(identity[:6]), ControlURL: "https://controlplane.tailscale.com", Logf: func(string, ...any) {}, UserLogf: func(string, ...any) {}}
	client.Transport.(*http.Transport).DialContext = server.Dial
	n := &node{server: server, client: client, ctx: ctx, cancel: cancel, origin: strings.TrimRight(cmd.Origin, "/"), directory: directory, status: Snapshot{State: "Starting"}, started: make(chan struct{})}
	var random [24]byte
	if _, err := rand.Read(random[:]); err != nil {
		cancel()
		return nil, err
	}
	handle := hex.EncodeToString(random[:])
	registry.nodes[handle] = n
	go n.monitor()
	return map[string]string{"handle": handle}, nil
}

// ipnlocal's Android socket-statistics initializer calls logpolicy.LogsDir even
// when log uploads are disabled. Android apps have no HOME or writable /tmp.
// Configure this before Server.Start, using only the app-private node directory.
func prepareLogDirectory(directory string) error {
	logs := filepath.Join(directory, "logs")
	if err := os.MkdirAll(logs, 0700); err != nil {
		return errors.New("Cannot create private connection log state")
	}
	if err := os.Chmod(logs, 0700); err != nil {
		return errors.New("Cannot protect private connection log state")
	}
	if err := os.Setenv("TS_LOGS_DIR", logs); err != nil {
		return errors.New("Cannot configure private connection log state")
	}
	return nil
}

func (n *node) monitor() {
	err := n.server.Start()
	close(n.started)
	if err != nil {
		n.setStatus(Snapshot{State: "Error", Error: "Tailscale could not start"})
		return
	}
	lc, err := n.server.LocalClient()
	if err != nil {
		n.setStatus(Snapshot{State: "Error", Error: "Tailscale status is unavailable"})
		return
	}
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		ctx, cancel := context.WithTimeout(n.ctx, 3*time.Second)
		status, err := lc.StatusWithoutPeers(ctx)
		cancel()
		if err == nil {
			snapshot := Snapshot{State: status.BackendState}
			if validAuthURL(status.AuthURL) {
				snapshot.AuthURL = status.AuthURL
			}
			n.setStatus(snapshot)
		}
		select {
		case <-n.ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (n *node) setStatus(status Snapshot) { n.mu.Lock(); n.status = status; n.mu.Unlock() }

func validAuthURL(value string) bool {
	u, err := url.Parse(value)
	return err == nil && u.Scheme == "https" && u.Host == "login.tailscale.com" && u.User == nil && strings.HasPrefix(u.Path, "/a/") && u.RawQuery == "" && u.Fragment == ""
}

type dialFunc func(context.Context, string, string) (net.Conn, error)

func httpClient(dial dialFunc, hostname, pin, roots string) (*http.Client, error) {
	config := &tls.Config{MinVersion: tls.VersionTLS12, ServerName: hostname}
	if pin != "" {
		if raw, err := hex.DecodeString(pin); err != nil || len(raw) != sha256.Size {
			return nil, errors.New("Invalid certificate fingerprint")
		}
		// Exact leaf pins intentionally support self-hosted endpoints at VPN IPs.
		config.InsecureSkipVerify = true
		config.VerifyConnection = func(state tls.ConnectionState) error {
			if len(state.PeerCertificates) == 0 {
				return errors.New("Server sent no certificate")
			}
			leaf := state.PeerCertificates[0]
			hash := sha256.Sum256(leaf.Raw)
			if hex.EncodeToString(hash[:]) != pin {
				return errors.New("Server certificate fingerprint changed")
			}
			if now := time.Now(); now.Before(leaf.NotBefore) || now.After(leaf.NotAfter) {
				return errors.New("Server certificate expired or not yet valid")
			}
			return nil
		}
	} else {
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM([]byte(roots)) {
			return nil, errors.New("Android trusted certificates are unavailable")
		}
		config.RootCAs = pool
	}
	transport := &http.Transport{DialContext: dial, TLSClientConfig: config, TLSHandshakeTimeout: 10 * time.Second, ResponseHeaderTimeout: 20 * time.Second, DisableKeepAlives: true, MaxResponseHeaderBytes: 1 << 20}
	return &http.Client{Transport: transport, Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}, nil
}

func (n *node) request(cmd Command) (any, error) {
	n.mu.Lock()
	state := n.status.State
	n.mu.Unlock()
	if state != "Running" || n.ctx.Err() != nil {
		return nil, errors.New("Private connection is not running; open connection settings")
	}
	if cmd.Method != "GET" && cmd.Method != "POST" && cmd.Method != "PUT" && cmd.Method != "DELETE" {
		return nil, errors.New("Unsupported workspace method")
	}
	if strings.ContainsAny(cmd.Path, "\r\n#") || strings.HasPrefix(cmd.Path, "/") {
		return nil, errors.New("Invalid workspace path")
	}
	// A streaming body has no GetBody callback: transport cannot replay a POST.
	var body io.Reader
	if cmd.Body != "" {
		body = io.NopCloser(strings.NewReader(cmd.Body))
	}
	request, err := http.NewRequestWithContext(n.ctx, cmd.Method, n.origin+"/api/"+cmd.Path, body)
	if err != nil {
		return nil, errors.New("Invalid workspace request")
	}
	request.Header.Set("Authorization", "Bearer "+cmd.Token)
	request.Header.Set("Content-Type", "application/json")
	response, err := n.client.Do(request)
	if err != nil {
		var certificateError *tls.CertificateVerificationError
		if errors.As(err, &certificateError) {
			return nil, errors.New("HTTPS certificate verification failed")
		}
		return nil, errors.New("Private workspace request failed; its result may be unknown")
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, maximumBytes+1))
	if err != nil {
		return nil, errors.New("Response interrupted; request was not replayed")
	}
	if len(raw) > maximumBytes {
		return nil, errors.New("Workspace response exceeds the mobile limit")
	}
	return map[string]any{"status": response.StatusCode, "body": string(raw)}, nil
}

func updateInterfaces(cmd Command) error {
	values := make([]netmon.Interface, 0, len(cmd.Interfaces))
	for _, item := range cmd.Interfaces {
		if item.Index <= 0 || item.Name == "" {
			return errors.New("Invalid Android network interface")
		}
		addresses := make([]net.Addr, 0, len(item.Addresses))
		for _, value := range item.Addresses {
			ip, prefix, err := net.ParseCIDR(value)
			if err != nil {
				return fmt.Errorf("Invalid Android interface address")
			}
			prefix.IP = ip
			addresses = append(addresses, prefix)
		}
		values = append(values, netmon.Interface{Interface: &net.Interface{Index: item.Index, MTU: item.MTU, Name: item.Name, Flags: net.Flags(item.Flags)}, AltAddrs: addresses})
	}
	interfaces.Lock()
	interfaces.values = values
	interfaces.Unlock()
	interfaceGetter.Do(func() {
		netmon.RegisterInterfaceGetter(func() ([]netmon.Interface, error) {
			interfaces.RLock()
			defer interfaces.RUnlock()
			return append([]netmon.Interface(nil), interfaces.values...), nil
		})
	})
	updateDefaultRoute(cmd.DefaultInterface)
	registry.Lock()
	nodes := make([]*node, 0, len(registry.nodes))
	for _, n := range registry.nodes {
		nodes = append(nodes, n)
	}
	registry.Unlock()
	for _, n := range nodes {
		go func(n *node) {
			select {
			case <-n.started:
			case <-n.ctx.Done():
				return
			}
			ctx, cancel := context.WithTimeout(n.ctx, 3*time.Second)
			defer cancel()
			if lc, err := n.server.LocalClient(); err == nil {
				_ = lc.DebugAction(ctx, "rebind")
			}
		}(n)
	}
	return nil
}
