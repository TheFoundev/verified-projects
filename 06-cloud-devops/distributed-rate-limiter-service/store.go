package main

import (
	"bufio"
	"fmt"
	"net"
	"strconv"
	"strings"
	"sync"
	"time"
)

// ---------------------------------------------------------------------------
// The store
// ---------------------------------------------------------------------------

// Store is where the counters of the limiter live.
//
// The in-memory store is used when the limiter runs on its own, and the Redis
// store when several instances of the service share one limit. The interface
// is the same for both, so the limiter does not know the difference.
type Store interface {
	// Take counts one request for a key and says whether it is allowed.
	Take(key string, now time.Time) (allowed bool, remaining int, err error)
	// Remaining reports how many requests are left for a key.
	Remaining(key string, now time.Time) (int, error)
	// Sweep removes the keys that are not used any more.
	Sweep(now time.Time) (int, error)
	// Name identifies the store.
	Name() string
	// Close releases what the store holds.
	Close() error
}

// bucket is one token bucket for one key.
type bucket struct {
	tokens float64
	last   time.Time
}

// MemoryStore keeps the counters in the process.
type MemoryStore struct {
	mu      sync.Mutex
	buckets map[string]*bucket
	rate    float64
	burst   float64
	ttl     time.Duration
	allowed int
	blocked int
}

// NewMemoryStore creates a store with the policy of the limiter.
func NewMemoryStore(rate, burst float64, ttl time.Duration) *MemoryStore {
	return &MemoryStore{
		buckets: map[string]*bucket{},
		rate:    rate,
		burst:   burst,
		ttl:     ttl,
	}
}

// Take counts one request for a key.
func (store *MemoryStore) Take(key string, now time.Time) (bool, int, error) {
	store.mu.Lock()
	defer store.mu.Unlock()

	entry, ok := store.buckets[key]
	if !ok {
		entry = &bucket{tokens: store.burst, last: now}
		store.buckets[key] = entry
	}
	elapsed := now.Sub(entry.last).Seconds()
	if elapsed > 0 {
		entry.tokens = min(store.burst, entry.tokens+elapsed*store.rate)
		entry.last = now
	}
	if entry.tokens >= 1 {
		entry.tokens--
		store.allowed++
		return true, int(entry.tokens), nil
	}
	store.blocked++
	return false, 0, nil
}

// Remaining reports the tokens left for a key.
func (store *MemoryStore) Remaining(key string, now time.Time) (int, error) {
	store.mu.Lock()
	defer store.mu.Unlock()

	entry, ok := store.buckets[key]
	if !ok {
		return int(store.burst), nil
	}
	elapsed := now.Sub(entry.last).Seconds()
	if elapsed > 0 {
		entry.tokens = min(store.burst, entry.tokens+elapsed*store.rate)
		entry.last = now
	}
	return int(entry.tokens), nil
}

// Sweep removes the keys that were not used for a while.
func (store *MemoryStore) Sweep(now time.Time) (int, error) {
	store.mu.Lock()
	defer store.mu.Unlock()

	removed := 0
	for key, entry := range store.buckets {
		if now.Sub(entry.last) > store.ttl {
			delete(store.buckets, key)
			removed++
		}
	}
	return removed, nil
}

// Name identifies the store.
func (store *MemoryStore) Name() string { return "memory" }

// Close releases what the store holds.
func (store *MemoryStore) Close() error { return nil }

// Counters reports the allowed and the blocked requests.
func (store *MemoryStore) Counters() (int, int, int) {
	store.mu.Lock()
	defer store.mu.Unlock()
	return store.allowed, store.blocked, len(store.buckets)
}

// ---------------------------------------------------------------------------
// The Redis client
// ---------------------------------------------------------------------------

// respClient is a very small client for the Redis protocol.
//
// Only the commands the limiter needs are there: INCR, PEXPIRE, GET, DEL and
// PING. The client is written here so the project has no dependency outside
// the standard library.
type respClient struct {
	mu     sync.Mutex
	conn   net.Conn
	reader *bufio.Reader
	host   string
}

// dialRedis opens a connection to a Redis server.
func dialRedis(address string) (*respClient, error) {
	conn, err := net.DialTimeout("tcp", address, 3*time.Second)
	if err != nil {
		return nil, err
	}
	return &respClient{conn: conn, reader: bufio.NewReader(conn), host: address}, nil
}

// call sends a command and reads the answer.
func (client *respClient) call(args ...string) (string, error) {
	client.mu.Lock()
	defer client.mu.Unlock()

	var builder strings.Builder
	builder.WriteString(fmt.Sprintf("*%d\r\n", len(args)))
	for _, argument := range args {
		builder.WriteString(fmt.Sprintf("$%d\r\n%s\r\n", len(argument), argument))
	}
	if _, err := client.conn.Write([]byte(builder.String())); err != nil {
		return "", err
	}
	return client.readReply()
}

// readReply reads one answer of the Redis protocol.
func (client *respClient) readReply() (string, error) {
	line, err := client.reader.ReadString('\n')
	if err != nil {
		return "", err
	}
	line = strings.TrimRight(line, "\r\n")
	if line == "" {
		return "", fmt.Errorf("empty answer from redis")
	}

	switch line[0] {
	case '+':
		return line[1:], nil
	case '-':
		return "", fmt.Errorf("redis: %s", line[1:])
	case ':':
		return line[1:], nil
	case '$':
		size, err := strconv.Atoi(line[1:])
		if err != nil {
			return "", err
		}
		if size < 0 {
			return "", nil
		}
		buffer := make([]byte, size+2)
		if _, err := readFull(client.reader, buffer); err != nil {
			return "", err
		}
		return string(buffer[:size]), nil
	default:
		return "", fmt.Errorf("unexpected answer from redis: %s", line)
	}
}

// readFull fills the buffer.
func readFull(reader *bufio.Reader, buffer []byte) (int, error) {
	read := 0
	for read < len(buffer) {
		count, err := reader.Read(buffer[read:])
		if err != nil {
			return read, err
		}
		read += count
	}
	return read, nil
}

// ping checks that the server answers.
func (client *respClient) ping() error {
	_, err := client.call("PING")
	return err
}

// close shuts the connection down.
func (client *respClient) close() error {
	return client.conn.Close()
}

// ---------------------------------------------------------------------------
// The Redis store
// ---------------------------------------------------------------------------

// RedisStore keeps the counters in a Redis server, so several instances of
// the service share one limit.
type RedisStore struct {
	client *respClient
	rate   float64
	burst  float64
	window time.Duration
	prefix string
}

// NewRedisStore connects to a Redis server.
func NewRedisStore(address string, rate, burst float64, window time.Duration) (*RedisStore, error) {
	client, err := dialRedis(address)
	if err != nil {
		return nil, err
	}
	if err := client.ping(); err != nil {
		client.close()
		return nil, err
	}
	return &RedisStore{
		client: client,
		rate:   rate,
		burst:  burst,
		window: window,
		prefix: "ratelimit:",
	}, nil
}

// key is the name of the counter of a key in Redis.
func (store *RedisStore) key(name string) string {
	return store.prefix + name
}

// Take counts one request for a key.
//
// The counter of the window is used, so every instance of the service counts
// into the same number.
func (store *RedisStore) Take(name string, now time.Time) (bool, int, error) {
	counter, err := store.client.call("INCR", store.key(name))
	if err != nil {
		return false, 0, err
	}
	count, err := strconv.Atoi(counter)
	if err != nil {
		return false, 0, err
	}
	if count == 1 {
		// The window starts with the first request.
		if _, err := store.client.call(
			"PEXPIRE", store.key(name), strconv.FormatInt(store.window.Milliseconds(), 10),
		); err != nil {
			return false, 0, err
		}
	}
	limit := int(store.burst)
	if limit < 1 {
		limit = 1
	}
	if count <= limit {
		return true, limit - count, nil
	}
	return false, 0, nil
}

// Remaining reports how many requests are left for a key.
func (store *RedisStore) Remaining(name string, now time.Time) (int, error) {
	answer, err := store.client.call("GET", store.key(name))
	if err != nil {
		return 0, err
	}
	if answer == "" {
		return int(store.burst), nil
	}
	count, err := strconv.Atoi(answer)
	if err != nil {
		return 0, err
	}
	remaining := int(store.burst) - count
	if remaining < 0 {
		remaining = 0
	}
	return remaining, nil
}

// Sweep does nothing: Redis expires the counters itself.
func (store *RedisStore) Sweep(now time.Time) (int, error) { return 0, nil }

// Name identifies the store.
func (store *RedisStore) Name() string { return "redis" }

// Close shuts the connection down.
func (store *RedisStore) Close() error { return store.client.close() }
