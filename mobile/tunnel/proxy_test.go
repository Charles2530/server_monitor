package tunnel

import (
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestGatewayValidation(t *testing.T) {
	for _, raw := range []string{"http://mac.tail.ts.net", "https://evil.test", "https://mac.ts.net@evil.test", "https://mac.ts.net/path", "https://mac.ts.net?x=1", "https://mac.ts.net#x", "https://mac.ts.net:8443", "https://mac.ts.net/../"} {
		if _, err := validGateway(raw); err == nil {
			t.Fatalf("accepted %s", raw)
		}
	}
	if got, err := validGateway("https://mac.tail.ts.net/"); err != nil || got != "https://mac.tail.ts.net" {
		t.Fatal(got, err)
	}
}

func TestExpiredPairingNeverContactsGateway(t *testing.T) {
	calls := 0
	c := &Client{http: &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		return &http.Response{StatusCode: 400, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(`{"error":"request should not happen"}`))}, nil
	})}}
	_, err := c.Pair(`{"version":1,"gateway":"https://mac.tail.ts.net","ticket":"`+strings.Repeat("b", 43)+`","expires_at":1}`, "phone")
	if err == nil || !strings.Contains(err.Error(), "过期") || calls != 0 {
		t.Fatalf("expired pairing: error=%v, requests=%d", err, calls)
	}
}

func TestGatewayCheckIsReadOnlyAndPreservesTransportCause(t *testing.T) {
	calls := 0
	c := &Client{http: &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.Method != "GET" || r.URL.Path != "/health" || r.Header.Get("Authorization") != "" {
			t.Fatal("health probe must not pair or send credentials")
		}
		return nil, errors.New("test connection refused")
	})}}
	_, err := c.CheckGateway("https://mac.tail.ts.net")
	if err == nil || !strings.Contains(err.Error(), "test connection refused") || strings.Contains(err.Error(), "https://") || calls != 1 {
		t.Fatalf("connection diagnosis: %v; requests=%d", err, calls)
	}
}

func TestProxyRequiresBootstrapAndStripsCredentials(t *testing.T) {
	calls := 0
	remote := &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.URL.String() != "https://mac.tail.ts.net/api/state?x=1" || r.Header.Get("Authorization") != "Bearer device-token" || r.Header.Get("Cookie") != "" || r.Header.Get("Origin") != "" {
			t.Errorf("untrusted forwarding")
		}
		return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": []string{"application/json"}}, Body: io.NopCloser(strings.NewReader(`{"ok":true}`))}, nil
	})}
	p, err := newBridge("https://mac.tail.ts.net", "device-token", remote)
	if err != nil {
		t.Fatal(err)
	}
	defer p.close()
	local := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	base := strings.Split(p.startURL(), "/_mobile/start")[0]
	r, err := local.Get(base + "/api/state")
	if err != nil {
		t.Fatal(err)
	}
	r.Body.Close()
	if r.StatusCode != 401 || calls != 0 {
		t.Fatal("unauthorized proxy")
	}
	r, err = local.Get(p.startURL())
	if err != nil {
		t.Fatal(err)
	}
	r.Body.Close()
	cookies := r.Cookies()
	if r.StatusCode != 303 || len(cookies) != 1 || !cookies[0].HttpOnly {
		t.Fatal("bootstrap failed")
	}
	again, _ := local.Get(p.startURL())
	again.Body.Close()
	if again.StatusCode != 401 {
		t.Fatal("bootstrap replay")
	}
	req, _ := http.NewRequest("GET", base+"/api/state?x=1", nil)
	req.AddCookie(cookies[0])
	req.Header.Set("Authorization", "attacker")
	req.Header.Set("Origin", "https://evil.test")
	r, err = local.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	r.Body.Close()
	if r.StatusCode != 200 || calls != 1 {
		t.Fatal("authorized request failed")
	}
	req, _ = http.NewRequest("GET", base+"/api/state", nil)
	req.AddCookie(cookies[0])
	req.Host = "evil.test"
	r, _ = local.Do(req)
	r.Body.Close()
	if r.StatusCode != 403 || calls != 1 {
		t.Fatal("host bypass")
	}
}

func TestBridgePostOnceAndNoRedirect(t *testing.T) {
	calls := 0
	remote := &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.Method != "POST" {
			t.Fatal(r.Method)
		}
		b, _ := io.ReadAll(r.Body)
		if string(b) != `{"preview_id":"same"}` {
			t.Fatal(string(b))
		}
		return &http.Response{StatusCode: 302, Header: http.Header{"Location": []string{"https://evil.test"}}, Body: io.NopCloser(strings.NewReader(""))}, nil
	}), CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	p, err := newBridge("https://mac.tail.ts.net", "token", remote)
	if err != nil {
		t.Fatal(err)
	}
	defer p.close()
	req := httptest.NewRequest("POST", "http://"+p.address+"/api/acp/create", strings.NewReader(`{"preview_id":"same"}`))
	req.AddCookie(&http.Cookie{Name: cookieName, Value: p.session})
	w := httptest.NewRecorder()
	p.serve(w, req)
	if w.Code != 502 || calls != 1 {
		t.Fatal(w.Code, calls)
	}
}

func TestLocalPaths(t *testing.T) {
	for _, raw := range []string{"/mobile/pair", "//evil.test", "/static/../.env", "/api/%2e%2e/.env", "/foo"} {
		u, _ := url.Parse(raw)
		if allowedLocalPath(u.Path) {
			t.Fatal(raw)
		}
	}
}

func TestClientPreservesOriginForOperationRecovery(t *testing.T) {
	dir := t.TempDir()
	c, err := NewClient(dir, "test-phone")
	if err != nil {
		t.Fatal(err)
	}
	first, err := c.Open("https://mac.tail.ts.net", "token", strings.Repeat("a", 32))
	if err != nil {
		t.Fatal(err)
	}
	c.StopProxy()
	other, err := NewClient(dir, "test-phone")
	if err != nil {
		t.Fatal(err)
	}
	second, err := other.Open("https://mac.tail.ts.net", "token", strings.Repeat("a", 32))
	if err != nil {
		t.Fatal(err)
	}
	defer other.StopProxy()
	a, _ := url.Parse(first)
	b, _ := url.Parse(second)
	if a.Host != b.Host {
		t.Fatal("origin changed: pending action recovery in localStorage would be lost")
	}
	if a.RawQuery == b.RawQuery {
		t.Fatal("bootstrap secret reused")
	}
}

func TestPairUsesOnlyTicketAndStoresCanonicalGateway(t *testing.T) {
	calls := 0
	c := &Client{http: &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.URL.String() != "https://mac.tail.ts.net/mobile/pair" || r.Method != "POST" || r.Header.Get("Authorization") != "" {
			t.Fatal("unexpected pairing request")
		}
		body, _ := io.ReadAll(r.Body)
		if !strings.Contains(string(body), `"name":"phone"`) || !strings.Contains(string(body), `"ticket":`) {
			t.Fatal("missing ticket")
		}
		return &http.Response{StatusCode: 200, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(`{"host_id":"` + strings.Repeat("a", 32) + `","device_id":"123","token":"` + strings.Repeat("a", 43) + `","state":"pending"}`))}, nil
	})}}
	result, err := c.Pair(`{"version":1,"gateway":"https://mac.tail.ts.net/","ticket":"`+strings.Repeat("b", 43)+`"}`, "phone")
	if err != nil || !strings.Contains(result, `"gateway":"https://mac.tail.ts.net"`) {
		t.Fatal(result, err)
	}
	for _, bad := range []string{`{}`, `{"version":2}`, `{"version":1,"gateway":"https://evil.test","ticket":"` + strings.Repeat("b", 43) + `"}`} {
		if _, err := c.Pair(bad, "phone"); err == nil {
			t.Fatal("bad pair accepted")
		}
	}
	if calls != 1 {
		t.Fatal(calls)
	}
}

func TestDifferentHostOriginsSurviveInterruptedReset(t *testing.T) {
	dir := t.TempDir()
	c, err := NewClient(dir, "phone")
	if err != nil {
		t.Fatal(err)
	}
	first, err := c.Open("https://mac.tail.ts.net", "token", strings.Repeat("a", 32))
	if err != nil {
		t.Fatal(err)
	}
	c.StopProxy()
	// Simulate an Activity disappearing before WebStorage cleanup, then another host pairing.
	next, err := NewClient(dir, "phone")
	if err != nil {
		t.Fatal(err)
	}
	second, err := next.Open("https://mac.tail.ts.net", "newtoken", strings.Repeat("b", 32))
	if err != nil {
		t.Fatal(err)
	}
	next.StopProxy()
	a, _ := url.Parse(first)
	b, _ := url.Parse(second)
	if a.Host == b.Host {
		t.Fatal("different host database reused previous WebView origin")
	}
	third, err := next.Open("https://mac.tail.ts.net", "renewedtoken", strings.Repeat("a", 32))
	if err != nil {
		t.Fatal(err)
	}
	defer next.StopProxy()
	original, _ := url.Parse(third)
	if original.Host != a.Host {
		t.Fatal("same-host credential renewal lost recovery origin")
	}
}

func TestActivityCancellationStopsNativeControlRequest(t *testing.T) {
	started := make(chan struct{})
	done := make(chan error, 1)
	c := &Client{http: &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		close(started)
		<-r.Context().Done()
		return nil, r.Context().Err()
	})}}
	go func() { _, err := c.DeviceStatus("https://mac.tail.ts.net", "token"); done <- err }()
	<-started
	c.CancelRequests()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("request was not cancelled")
		}
	case <-time.After(time.Second):
		t.Fatal("activity request remained blocked")
	}
}
