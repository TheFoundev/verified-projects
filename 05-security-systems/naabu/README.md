# Naabu

A TCP connect port scanner with port range parsing.

> A fast Go port scanner that demonstrates asynchronous SYN scanning and pipeline-friendly output for reconnaissance tooling.

## Overview

An original, self-contained implementation of the **Naabu** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Port list and range parsing
- Concurrent TCP connect scans with timeouts
- Open, closed and filtered classification
- JSON results and a short summary

## Stack

`Go`
The CSV stack is Go. Raw SYN scans need privileges and libpcap, so this build uses TCP connect scans from the standard library.

## Run

```bash
go run . -host 127.0.0.1 -ports 22,80,8000-8010
go test ./...
```

## Structure

```text
naabu/
├── main.go                scanner CLI
├── scanner.go             port parsing and connect scan
├── scanner_test.go        unit tests
└── go.mod
```

## Testing

```bash
go test ./...
```

5 tests (`go test ./...`), up from 2: the hosts, the networks and the ports.


## Notes and limitations

- Connect scans are slower and noisier than SYN scans.
- Only IPv4 host names are supported.
- There is no service fingerprinting or OS detection.

## License

MIT. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
