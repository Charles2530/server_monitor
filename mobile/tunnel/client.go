// Package tunnel exposes only a small gomobile-compatible API to Android.
package tunnel

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"tailscale.com/tsnet"
)

type Client struct {
	mu          sync.Mutex
	network     *tsnet.Server
	http        *http.Client
	proxy       *bridge
	portFile    string
	started     atomic.Bool
	requestsMu  sync.Mutex
	requests    map[uint64]context.CancelFunc
	nextRequest uint64
}

func NewClient(stateDir, hostname string) (*Client, error) {
	if stateDir == "" || hostname == "" {
		return nil, errors.New("缺少网络状态目录或设备名")
	}
	if err := os.MkdirAll(stateDir, 0700); err != nil {
		return nil, err
	}
	if runtime.GOOS == "android" {
		if err := configureAndroidLogs(stateDir); err != nil {
			return nil, err
		}
	}
	network := &tsnet.Server{Dir: stateDir, Hostname: hostname, UserLogf: func(string, ...any) {}, Logf: func(string, ...any) {}}
	client := network.HTTPClient()
	client.Timeout = 95 * time.Second
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	return &Client{network: network, http: client, portFile: filepath.Join(stateDir, "bridge-port")}, nil
}
func (c *Client) Start() error {
	err := c.network.Start()
	if err == nil {
		c.started.Store(true)
	}
	return err
}

// CancelRequests stops native control requests when an Activity leaves; it never resubmits a write.
func (c *Client) CancelRequests() {
	c.requestsMu.Lock()
	defer c.requestsMu.Unlock()
	for _, cancel := range c.requests {
		cancel()
	}
}
func (c *Client) Login() error {
	local, err := c.network.LocalClient()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	return local.StartLoginInteractive(ctx)
}
func (c *Client) Status() (string, error) {
	local, err := c.network.LocalClient()
	if err != nil {
		return "", err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	status, err := local.StatusWithoutPeers(ctx)
	if err != nil {
		return "", err
	}
	data, err := json.Marshal(map[string]string{"state": status.BackendState, "auth_url": status.AuthURL})
	return string(data), err
}
func (c *Client) request(gateway, path, method, token, body string) (string, error) {
	base, err := validGateway(gateway)
	if err != nil {
		return "", err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	c.requestsMu.Lock()
	if c.requests == nil {
		c.requests = make(map[uint64]context.CancelFunc)
	}
	c.nextRequest++
	requestID := c.nextRequest
	c.requests[requestID] = cancel
	c.requestsMu.Unlock()
	defer func() { cancel(); c.requestsMu.Lock(); delete(c.requests, requestID); c.requestsMu.Unlock() }()
	req, err := http.NewRequestWithContext(ctx, method, base+path, strings.NewReader(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	response, err := c.http.Do(req)
	if err != nil {
		// Keep the transport cause (DNS, timeout, TLS) without repeating the request URL.
		var requestError *url.Error
		if errors.As(err, &requestError) {
			err = requestError.Err
		}
		return "", fmt.Errorf("无法连接电脑网关：%w", err)
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 65537))
	if err != nil || len(data) > 65536 {
		return "", errors.New("网关响应无效")
	}
	if response.StatusCode != 200 {
		var payload struct {
			Error string `json:"error"`
		}
		_ = json.Unmarshal(data, &payload)
		if payload.Error != "" {
			return "", errors.New(payload.Error)
		}
		return "", errors.New("网关拒绝了请求")
	}
	return string(data), nil
}
func (c *Client) Pair(payload, deviceName string) (string, error) {
	var config struct {
		Version   int     `json:"version"`
		Gateway   string  `json:"gateway"`
		Ticket    string  `json:"ticket"`
		ExpiresAt float64 `json:"expires_at"`
	}
	if len(payload) > 4096 || json.Unmarshal([]byte(payload), &config) != nil || config.Version != 1 || len(config.Ticket) != 43 {
		return "", errors.New("配对内容无效，请使用电脑新生成的二维码")
	}
	if config.ExpiresAt > 0 && config.ExpiresAt <= float64(time.Now().Unix()) {
		return "", errors.New("配对二维码已过期，请在电脑重新生成后再扫描")
	}
	gateway, err := validGateway(config.Gateway)
	if err != nil {
		return "", err
	}
	body, err := json.Marshal(map[string]string{"ticket": config.Ticket, "name": deviceName})
	if err != nil {
		return "", err
	}
	result, err := c.request(gateway, "/mobile/pair", "POST", "", string(body))
	if err != nil {
		return "", err
	}
	var device map[string]any
	if json.Unmarshal([]byte(result), &device) != nil {
		return "", errors.New("配对响应无效")
	}
	hostID, validHost := device["host_id"].(string)
	if !validHost || !regexp.MustCompile(`^[a-f0-9]{32}$`).MatchString(hostID) {
		return "", errors.New("电脑网关缺少有效主机身份，请更新网关后重新配对")
	}
	token, ok := device["token"].(string)
	if !ok || len(token) != 43 {
		return "", errors.New("配对凭证无效")
	}
	device["gateway"] = gateway
	data, err := json.Marshal(device)
	return string(data), err
}
func (c *Client) DeviceStatus(gateway, token string) (string, error) {
	return c.request(gateway, "/mobile/status", "GET", token, "")
}

// CheckGateway probes only the public health endpoint; it never redeems a ticket.
func (c *Client) CheckGateway(gateway string) (string, error) {
	return c.request(gateway, "/health", "GET", "", "")
}
func (c *Client) Open(gateway, token, hostID string) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.proxy != nil {
		c.proxy.close()
		c.proxy = nil
	}
	base, err := validGateway(gateway)
	if err != nil {
		return "", err
	}
	if !regexp.MustCompile(`^[a-f0-9]{32}$`).MatchString(hostID) {
		return "", errors.New("电脑主机身份无效，请重新配对")
	}
	namespace := fmt.Sprintf("%x", sha256.Sum256([]byte(base+"|"+hostID)))
	portFile := c.portFile + "-" + namespace
	address := "127.0.0.1:0"
	if data, err := os.ReadFile(portFile); err == nil {
		port, err := strconv.Atoi(strings.TrimSpace(string(data)))
		if err != nil || port < 1024 || port > 65535 {
			return "", errors.New("手机本地入口配置无效，未重置任务操作记录")
		}
		address = "127.0.0.1:" + strconv.Itoa(port)
	} else if !os.IsNotExist(err) {
		return "", err
	}
	var p *bridge
	for attempt := 0; attempt < 10; attempt++ {
		p, err = bridgeAt(gateway, token, c.http, address)
		if err != nil {
			return "", err
		}
		collision := false
		files, _ := filepath.Glob(c.portFile + "-*")
		for _, file := range files {
			if file == portFile {
				continue
			}
			other, _ := os.ReadFile(file)
			if "127.0.0.1:"+strings.TrimSpace(string(other)) == p.address {
				collision = true
				break
			}
		}
		if !collision {
			break
		}
		p.close()
		p = nil
	}
	if p == nil {
		return "", errors.New("无法分配隔离的手机入口，请稍后重试")
	}
	// A stable origin retains localStorage operation IDs after process restarts.
	port := strings.TrimPrefix(p.address, "127.0.0.1:")
	if err := os.WriteFile(portFile, []byte(port), 0600); err != nil {
		p.close()
		return "", err
	}
	c.proxy = p
	return p.startURL(), nil
}
func (c *Client) StopProxy() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.proxy != nil {
		c.proxy.close()
		c.proxy = nil
	}
}
func (c *Client) Close() error {
	c.StopProxy()
	c.http.CloseIdleConnections()
	return c.network.Close()
}
