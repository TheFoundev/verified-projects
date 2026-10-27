package main

import (
	"fmt"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// Limiter limits the requests of many keys.
//
// The counters live in a store, so several instances of the service can share
// one limit when the store is Redis.
type Limiter struct {
	store Store
	rate  float64
	burst float64
	ttl   time.Duration
	// TrustedProxies says that the address of the proxy in front of the
	// service may be trusted to set X-Forwarded-For.
	TrustedProxies bool
}

// NewLimiter creates a limiter with a store, a refill rate and a burst size.
func NewLimiter(store Store, rate, burst float64) *Limiter {
	if rate <= 0 {
		rate = 1
	}
	if burst < 1 {
		burst = 1
	}
	return &Limiter{store: store, rate: rate, burst: burst, ttl: 10 * time.Minute}
}

// AllowAt checks and consumes a token for a key at a given time.
func (limiter *Limiter) AllowAt(key string, now time.Time) bool {
	allowed, _, err := limiter.store.Take(key, now)
	if err != nil {
		// The store is not there: the request is let through rather than
		// refused, and the log of the service says so.
		return true
	}
	return allowed
}

// Allow checks a key using the current time.
func (limiter *Limiter) Allow(key string) bool {
	return limiter.AllowAt(key, time.Now())
}

// Remaining reports the tokens left for a key.
func (limiter *Limiter) Remaining(key string) int {
	return limiter.RemainingAt(key, time.Now())
}

// RemainingAt reports the tokens left for a key at a given time.
func (limiter *Limiter) RemainingAt(key string, now time.Time) int {
	remaining, err := limiter.store.Remaining(key, now)
	if err != nil {
		return 0
	}
	return remaining
}

// Sweep removes the keys that are not used any more.
func (limiter *Limiter) Sweep() int {
	removed, err := limiter.store.Sweep(time.Now())
	if err != nil {
		return 0
	}
	return removed
}

// Stats reports the counters of the limiter.
func (limiter *Limiter) Stats() map[string]int {
	stats := map[string]int{"keys": 0}
	if memory, ok := limiter.store.(*MemoryStore); ok {
		allowed, blocked, keys := memory.Counters()
		stats["allowed"] = allowed
		stats["blocked"] = blocked
		stats["keys"] = keys
	}
	stats["store"] = 0
	stats["rate"] = int(limiter.rate)
	stats["burst"] = int(limiter.burst)
	return stats
}

// KeyFor reports the key a request is counted under.
//
// The API key of the header comes first. Behind a proxy the address of the
// proxy would be the same for every client, so the forwarded address is used
// when the proxy is trusted.
func (limiter *Limiter) KeyFor(request *http.Request) string {
	if key := request.Header.Get("X-API-Key"); key != "" {
		return key
	}

	host := request.RemoteAddr
	if forwarded, _, err := net.SplitHostPort(host); err == nil {
		host = forwarded
	}

	if limiter.TrustedProxies {
		if forwarded := request.Header.Get("X-Forwarded-For"); forwarded != "" {
			// The first address is the one of the client.
			first := strings.TrimSpace(strings.Split(forwarded, ",")[0])
			if first != "" {
				host = first
			}
		}
	}

	return host
}

// Middleware limits the requests by header or remote address.
func (limiter *Limiter) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		key := limiter.KeyFor(request)
		if !limiter.Allow(key) {
			writer.Header().Set("Retry-After", "1")
			writer.Header().Set("X-RateLimit-Limit", strconv.FormatFloat(limiter.burst, 'f', 0, 64))
			writer.Header().Set("X-RateLimit-Remaining", strconv.Itoa(limiter.Remaining(key)))
			http.Error(writer, "rate limit exceeded", http.StatusTooManyRequests)
			return
		}
		writer.Header().Set("X-RateLimit-Limit", strconv.FormatFloat(limiter.burst, 'f', 0, 64))
		next.ServeHTTP(writer, request)
	})
}

// Prometheus writes the counters of the limiter in the text format of
// Prometheus.
func (limiter *Limiter) Prometheus() string {
	stats := limiter.Stats()
	lines := []string{
		"# HELP ratelimit_requests_total The requests the limiter counted.",
		"# TYPE ratelimit_requests_total counter",
		fmt.Sprintf("ratelimit_requests_total{outcome=\"allowed\"} %d", stats["allowed"]),
		fmt.Sprintf("ratelimit_requests_total{outcome=\"blocked\"} %d", stats["blocked"]),
		"# HELP ratelimit_keys The keys the limiter knows.",
		"# TYPE ratelimit_keys gauge",
		fmt.Sprintf("ratelimit_keys %d", stats["keys"]),
	}
	return strings.Join(lines, "\n") + "\n"
}

func min(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
