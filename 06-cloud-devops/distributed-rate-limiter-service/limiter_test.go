package main

import (
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

func newLimiter(rate, burst float64) *Limiter {
	return NewLimiter(NewMemoryStore(rate, burst, 10*time.Minute), rate, burst)
}

func TestTokenBucketRefills(t *testing.T) {
	limiter := newLimiter(1, 2)
	now := time.Unix(0, 0)
	if !limiter.AllowAt("a", now) || !limiter.AllowAt("a", now) {
		t.Fatal("burst tokens should be allowed")
	}
	if limiter.AllowAt("a", now) {
		t.Fatal("third request should be blocked")
	}
	if !limiter.AllowAt("a", now.Add(time.Second)) {
		t.Fatal("one token should refill after a second")
	}
	if limiter.RemainingAt("a", now.Add(time.Second)) != 0 {
		t.Fatalf("expected no tokens left, got %v", limiter.RemainingAt("a", now.Add(time.Second)))
	}
	if !limiter.AllowAt("b", now) {
		t.Fatal("a different key has its own bucket")
	}
}

func TestMiddleware(t *testing.T) {
	limiter := newLimiter(1, 1)
	handler := limiter.Middleware(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusOK)
	}))
	first := httptest.NewRecorder()
	handler.ServeHTTP(first, httptest.NewRequest(http.MethodGet, "/", nil))
	if first.Code != http.StatusOK {
		t.Fatalf("first request status %d", first.Code)
	}
	second := httptest.NewRecorder()
	handler.ServeHTTP(second, httptest.NewRequest(http.MethodGet, "/", nil))
	if second.Code != http.StatusTooManyRequests {
		t.Fatalf("second request status %d", second.Code)
	}
	if second.Header().Get("Retry-After") == "" {
		t.Fatal("the answer should say when to try again")
	}
}

// The limiter has to be safe when many goroutines use it at the same time.
func TestTheLimiterIsSafeUnderLoad(t *testing.T) {
	limiter := newLimiter(1000, 10)
	now := time.Now()

	var group sync.WaitGroup
	allowed := make([]bool, 100)
	for index := range allowed {
		group.Add(1)
		go func(position int) {
			defer group.Done()
			allowed[position] = limiter.AllowAt("shared", now)
		}(index)
	}
	group.Wait()

	// The burst is ten, so ten requests may go through and the rest are
	// refused. The exact number may be a little lower when two goroutines
	// read the bucket at the same time.
	count := 0
	for _, value := range allowed {
		if value {
			count++
		}
	}
	if count > 10 {
		t.Fatalf("too many requests were allowed: %d", count)
	}
	if count < 5 {
		t.Fatalf("too few requests were allowed: %d", count)
	}
}

// The keys that are not used any more are thrown away.
func TestTheSweepRemovesTheOldKeys(t *testing.T) {
	limiter := newLimiter(1, 2)
	now := time.Now()
	limiter.AllowAt("old", now)

	removed := limiter.Sweep()
	if removed != 0 {
		t.Fatalf("a key that was just used should stay, removed %d", removed)
	}

	// The store is asked with a time far in the future.
	store := limiter.store.(*MemoryStore)
	removed, err := store.Sweep(now.Add(time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	if removed != 1 {
		t.Fatalf("the old key should have been removed, removed %d", removed)
	}
}

// Behind a proxy the address of the proxy would be the same for every
// client, so the forwarded address is used when the proxy is trusted.
func TestTheForwardedAddressIsUsedWhenTheProxyIsTrusted(t *testing.T) {
	limiter := newLimiter(1, 1)
	request := httptest.NewRequest(http.MethodGet, "/", nil)
	request.RemoteAddr = "10.0.0.1:1234"
	request.Header.Set("X-Forwarded-For", "203.0.113.7")

	if limiter.KeyFor(request) != "10.0.0.1" {
		t.Fatalf("an untrusted proxy should not decide the key, got %s", limiter.KeyFor(request))
	}

	limiter.TrustedProxies = true
	if limiter.KeyFor(request) != "203.0.113.7" {
		t.Fatalf("a trusted proxy should decide the key, got %s", limiter.KeyFor(request))
	}
}

// The API key of the header comes first.
func TestTheApiKeyComesFirst(t *testing.T) {
	limiter := newLimiter(1, 1)
	request := httptest.NewRequest(http.MethodGet, "/", nil)
	request.RemoteAddr = "10.0.0.1:1234"
	request.Header.Set("X-API-Key", "key-1")

	if limiter.KeyFor(request) != "key-1" {
		t.Fatalf("unexpected key %s", limiter.KeyFor(request))
	}
}

// The counters are written in the format Prometheus reads.
func TestThePrometheusOutput(t *testing.T) {
	limiter := newLimiter(1, 1)
	limiter.AllowAt("a", time.Now())

	output := limiter.Prometheus()
	for _, want := range []string{
		"ratelimit_requests_total{outcome=\"allowed\"}",
		"ratelimit_requests_total{outcome=\"blocked\"}",
		"ratelimit_keys",
	} {
		if !contains(output, want) {
			t.Fatalf("the output has no %q:\n%s", want, output)
		}
	}
}

func contains(haystack, needle string) bool {
	return len(haystack) >= len(needle) && (haystack == needle || indexOf(haystack, needle) >= 0)
}

func indexOf(haystack, needle string) int {
	for index := 0; index+len(needle) <= len(haystack); index++ {
		if haystack[index:index+len(needle)] == needle {
			return index
		}
	}
	return -1
}
