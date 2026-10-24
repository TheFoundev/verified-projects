# Coraza

A small web application firewall that inspects requests against rules.

> An open-source Web Application Firewall engine that is embedded to filter HTTP traffic in real time.

## Overview

An original, self-contained implementation of the **Coraza** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Regex rules for SQL injection, XSS, traversal and scanners
- Per-request findings with severity and evidence
- Allow or block decision with a score
- Command line inspection and JSON output

## Stack

`Go`
The CSV stack is Go. This build implements a compact rule engine with Go's regexp package instead of loading the full OWASP Core Rule Set.

## Run

```bash
go run . -path /items -query 'id=1 union select 1'
go test ./...
```

## Structure

```text
coraza/
├── main.go                request inspection CLI
├── waf.go                 rules and decision logic
├── waf_test.go            unit tests
└── go.mod
```

## Testing

```bash
go test ./...
```

5 tests (`go test ./...`), up from 2: the middleware, a suspicious request, a request that is written with escapes, the body and the request that is looked at.


## Notes and limitations

- The rule set is tiny and will miss most real attacks.
- Requests are inspected as strings; there is no HTTP middleware or body streaming.
- There is no anomaly scoring across sessions or rule tuning.

## License

Apache-2.0. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
