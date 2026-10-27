package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	rate := flag.Float64("rate", 2, "tokens per second")
	burst := flag.Float64("burst", 3, "bucket size")
	redis := flag.String("redis", "", "the address of a Redis server, for a shared limit")
	window := flag.Duration("window", time.Minute, "the window of the shared limit")
	port := flag.Int("port", 8080, "the port to serve on")
	trusted := flag.Bool("trusted-proxy", false, "trust the X-Forwarded-For header")
	requests := flag.Int("requests", 0, "requests to simulate instead of serving")
	flag.Parse()

	var store Store
	if *redis != "" {
		redisStore, err := NewRedisStore(*redis, *rate, *burst, *window)
		if err != nil {
			fmt.Fprintln(os.Stderr, "could not reach redis:", err)
			os.Exit(1)
		}
		defer redisStore.Close()
		store = redisStore
	} else {
		store = NewMemoryStore(*rate, *burst, 10*time.Minute)
	}

	limiter := NewLimiter(store, *rate, *burst)
	limiter.TrustedProxies = *trusted

	if *requests > 0 {
		now := time.Now()
		for index := 0; index < *requests; index++ {
			allowed := limiter.AllowAt("client-1", now.Add(time.Duration(index)*200*time.Millisecond))
			fmt.Printf("request %d allowed=%v remaining=%d\n", index+1, allowed, limiter.Remaining("client-1"))
		}
		fmt.Println("statistics:", limiter.Stats())
		return
	}

	mux := http.NewServeMux()
	mux.Handle("/v1/check", limiter.Middleware(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		key := limiter.KeyFor(request)
		writer.Header().Set("Content-Type", "application/json")
		json.NewEncoder(writer).Encode(map[string]any{
			"key":       key,
			"allowed":   true,
			"remaining": limiter.Remaining(key),
			"store":     store.Name(),
		})
	})))
	mux.HandleFunc("/metrics", func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "text/plain; version=0.0.4")
		fmt.Fprint(writer, limiter.Prometheus())
	})
	mux.HandleFunc("/healthz", func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		json.NewEncoder(writer).Encode(map[string]any{"ok": true, "store": store.Name()})
	})

	// The keys that are not used any more are thrown away every minute.
	go func() {
		ticker := time.NewTicker(time.Minute)
		defer ticker.Stop()
		for range ticker.C {
			limiter.Sweep()
		}
	}()

	server := &http.Server{Addr: fmt.Sprintf(":%d", *port), Handler: mux}
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-stop
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		server.Shutdown(shutdown)
	}()

	fmt.Printf("the rate limiter is listening on http://127.0.0.1:%d (store: %s)\n", *port, store.Name())
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
