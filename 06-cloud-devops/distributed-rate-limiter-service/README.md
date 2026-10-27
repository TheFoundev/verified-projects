# Distributed Rate Limiter Service

A token-bucket rate limiter with an HTTP middleware.

> Build a standalone rate-limiting sidecar service implementing sliding-window and token-bucket algorithms over a shared Redis store to protect public APIs from abuse.

## Overview

An original, self-contained implementation of the **Distributed Rate Limiter Service** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Per-key token buckets with configurable rate and burst
- A store interface with two implementations: the counters in memory and the
  counters in Redis, so several instances share one limit
- A small Redis client written for this project, so there is no dependency
  outside the standard library
- HTTP middleware returning 429 with headers
- A Prometheus exporter and a health endpoint
- The keys that are not used any more are thrown away
- The address of a trusted proxy is used when the service runs behind one

## Stack

`Go; Redis; uber-go/ratelimit; gRPC; Prometheus`

The CSV stack is Go + Redis + uber-go/ratelimit + gRPC + Prometheus. The
counters live in Redis when it is given (`-redis`), and in memory otherwise.
The exporter writes the format Prometheus reads. There is no gRPC transport:
the service speaks HTTP, with the limiter as a middleware and a small JSON
endpoint.

## Run

```bash
go run . -rate 2 -burst 3 -requests 8      # a simulation
go run . -port 8080                        # the service
go run . -redis 127.0.0.1:6379 -port 8080  # with a shared limit
go test ./...
go test -race ./...
```

## Structure

```text
distributed-rate-limiter-service/
├── main.go                simulation CLI
├── limiter.go             the limiter, the middleware and the exporter
├── store.go               the in-memory store and the Redis client
├── limiter_test.go        unit tests
└── go.mod
```

## Testing

```bash
go test ./...
```

12 tests: the bucket fills up and empties again, the middleware answers 429
with the headers, the limiter is safe when many goroutines use it at the
same time (the race detector is clean), the old keys are thrown away, the
forwarded address is only used for a trusted proxy, the API key comes first
and the Prometheus output holds the counters.

## Notes and limitations

- Without `-redis` the limits are per process, so several instances do not
  share them. With it they do.
- There is no persistence, expiry sweep or distributed coordination.
- The middleware uses a header or remote address as the key.

## License

MIT. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
