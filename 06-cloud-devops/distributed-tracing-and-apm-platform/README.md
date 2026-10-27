# Distributed Tracing and APM Platform

Collect spans, assemble traces and compute latency percentiles.

> Deploy an OpenTelemetry-native APM that captures traces, logs and metrics from instrumented services and correlates them for latency and error root-cause analysis.

## Overview

An original, self-contained implementation of the **Distributed Tracing and APM Platform** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Span model with parent relationships
- Trace assembly into a tree and a total duration
- Per-service latency percentiles
- JSON ingestion and query handlers

## Stack

`Go`
The CSV stack is Go. This build implements a small span store and HTTP query API; there is no OpenTelemetry exporter, Kafka or columnar store.

## Run

```bash
go run .
go run . -listen :8081
go test ./...
```

## Structure

```text
distributed-tracing-and-apm-platform/
├── main.go                demo and server entry point
├── tracing.go             span store, assembly and handlers
├── tracing_test.go        unit tests
└── go.mod
```

## Testing

```bash
go test ./...
```

7 tests (`go test ./...`), up from 2: the traces, the stats, the percentile and the endpoint of the OpenTelemetry protocol.


## Notes and limitations

- Spans are held in memory and lost on restart.
- Sampling, context propagation and OpenTelemetry formats are not implemented.
- There is no frontend; the API returns JSON.

## License

MIT. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
