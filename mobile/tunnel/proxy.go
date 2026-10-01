package tunnel

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"
)

const cookieName = "charles_mobile_session"
const maxBody = 131072
const maxResponse = 32 * 1024 * 1024

var gatewayHost = regexp.MustCompile(`^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net$`)

func validGateway(raw string) (string, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.User != nil || !gatewayHost.MatchString(u.Hostname()) || (u.Port() != "" && u.Port() != "443") || (u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" {
		return "", errors.New("网关必须是 Tailscale Serve 的 HTTPS 根地址")
	}
	return "https://" + u.Hostname(), nil
}
func secret() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}
func equal(a, b string) bool { return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1 }
func allowedLocalPath(path string) bool {
	if strings.Contains(path, "..") || strings.ContainsAny(path, "\\%") || strings.Contains(path, "//") {
		return false
	}
	return path == "/" || path == "/terminal" || strings.HasPrefix(path, "/api/") || strings.HasPrefix(path, "/static/")
}
func jsonError(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	payload := map[string]string{"error": message}
	if status == 401 || status == 403 {
		payload["code"] = "mobile_connection_required"
	}
	_ = json.NewEncoder(w).Encode(payload)
}

type bridge struct {
	mu                                          sync.Mutex
	address, gateway, token, session, bootstrap string
	bootUsed                                    bool
	server                                      *http.Server
	client                                      *http.Client
}

func newBridge(gateway, token string, client *http.Client) (*bridge, error) {
	return bridgeAt(gateway, token, client, "127.0.0.1:0")
}

func bridgeAt(gateway, token string, client *http.Client, address string) (*bridge, error) {
	gateway, err := validGateway(gateway)
	if err != nil {
		return nil, err
	}
	if token == "" {
		return nil, errors.New("手机尚未配对")
	}
	session, err := secret()
	if err != nil {
		return nil, err
	}
	boot, err := secret()
	if err != nil {
		return nil, err
	}
	listener, err := net.Listen("tcp4", address)
	if err != nil {
		return nil, err
	}
	p := &bridge{address: listener.Addr().String(), gateway: gateway, token: token, session: session, bootstrap: boot, client: client}
	p.server = &http.Server{Handler: http.HandlerFunc(p.serve), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 100 * time.Second, IdleTimeout: 30 * time.Second}
	go func() { _ = p.server.Serve(listener) }()
	return p, nil
}
func (p *bridge) startURL() string {
	return "http://" + p.address + "/_mobile/start?ticket=" + url.QueryEscape(p.bootstrap)
}
func (p *bridge) close() { _ = p.server.Close() }
func (p *bridge) serve(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if r.Host != p.address {
		jsonError(w, 403, "本机入口无效")
		return
	}
	if r.URL.Path == "/_mobile/start" && r.Method == "GET" {
		p.mu.Lock()
		ok := !p.bootUsed && equal(r.URL.Query().Get("ticket"), p.bootstrap)
		if ok {
			p.bootUsed = true
		}
		p.mu.Unlock()
		if !ok {
			jsonError(w, 401, "启动入口已失效，请回到 App 重新打开")
			return
		}
		http.SetCookie(w, &http.Cookie{Name: cookieName, Value: p.session, Path: "/", HttpOnly: true, SameSite: http.SameSiteStrictMode})
		http.Redirect(w, r, "/", http.StatusSeeOther)
		return
	}
	cookie, err := r.Cookie(cookieName)
	if err != nil || !equal(cookie.Value, p.session) {
		jsonError(w, 401, "请从 App 进入监控")
		return
	}
	if (r.Method != "GET" && r.Method != "POST") || !allowedLocalPath(r.URL.Path) {
		jsonError(w, 404, "未开放此接口")
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, maxBody+1))
	if err != nil || len(body) > maxBody {
		jsonError(w, 413, "请求过长")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 95*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, r.Method, p.gateway+r.URL.RequestURI(), strings.NewReader(string(body)))
	if err != nil {
		jsonError(w, 400, "请求格式无效")
		return
	}
	req.Header.Set("Authorization", "Bearer "+p.token)
	for _, key := range []string{"Content-Type", "X-Monitor-Site", "X-Monitor-Request"} {
		if v := r.Header.Get(key); v != "" {
			req.Header.Set(key, v)
		}
	}
	response, err := p.client.Do(req)
	if err != nil {
		message := "电脑暂时离线或连接已中断，请回到连接页面检查"
		if r.Method == "POST" {
			message = "连接中断，操作结果未知；请核对任务后再操作，勿重复提交"
		}
		jsonError(w, 503, message)
		return
	}
	defer response.Body.Close()
	if response.StatusCode >= 300 && response.StatusCode < 400 {
		jsonError(w, 502, "网关返回了非预期跳转")
		return
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, maxResponse+1))
	if err != nil || len(data) > maxResponse {
		jsonError(w, 502, "后台响应中断或过大")
		return
	}
	for _, key := range []string{"Content-Type", "Content-Security-Policy"} {
		if v := response.Header.Get(key); v != "" {
			w.Header().Set(key, v)
		}
	}
	w.WriteHeader(response.StatusCode)
	_, _ = w.Write(data)
}
