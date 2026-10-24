# Auto-Configuring Reverse Proxy with Service Discovery

A reverse proxy that discovers healthy backends from a config file.

> Build a reverse proxy that discovers containers via the Docker API, auto-generates routing rules and TLS certificates, and exposes a dashboard for self-hosted services.

## Overview

An original, self-contained implementation of the **Auto-Configuring Reverse Proxy with Service Discovery** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Small YAML-ish service config parser
- Round-robin selection across healthy instances
- HTTP reverse proxy with path prefixes
- Health check endpoint and instance state

## Stack

`Go`
The CSV stack is Go. This build implements discovery and proxying with net/http and httputil; there is no Docker API, ACME or dynamic TLS.

## Run

```bash
go run .
go test ./...
```

## Structure

```text
auto-configuring-reverse-proxy-with-service-discovery/
├── main.go                proxy server entry point
├── proxy.go               config, discovery and reverse proxy
├── proxy_test.go          unit tests
└── go.mod
```

## Testing

```bash
go test ./...
```

9 tests: the config, the round robin, the health endpoint, an unknown
service, a path without a service, a broken config, the counter, the
discovery from Docker and the health of an instance.

## Notes and limitations

- The config parser handles only the small example format, not full YAML.
- Health checks are explicit calls, not a background probe loop.
- TLS certificates are not managed.

## License

MIT. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
