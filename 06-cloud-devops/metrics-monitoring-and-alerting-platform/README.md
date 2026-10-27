# Metrics Monitoring and Alerting Platform

Store time-series samples and evaluate alert rules.

> Instrument services with a pull-based metrics pipeline, write PromQL queries and a rule-based alerting system with routing, silencing and dead-man notifications.

## Overview

An original, self-contained implementation of the **Metrics Monitoring and Alerting Platform** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Metric samples with labels and timestamps
- Rate, sum and average queries over a time window
- Alert rules with a threshold and a for-duration
- JSON ingestion and query API

## Stack

`Go`
The CSV stack is Go. This build implements a tiny in-memory time series store and alert evaluator instead of the Prometheus stack.

## Run

```bash
go run .
go run . -listen :8083
go test ./...
```

## Structure

```text
metrics-monitoring-and-alerting-platform/
├── main.go                demo and server entry point
├── metrics.go             store, queries and alerts
├── metrics_test.go        unit tests
└── go.mod
```

## Testing

```bash
go test ./...
```

5 tests (`go test ./...`), up from 2: the rate, the sum, the window and the exporter.


## Notes and limitations

- Only rate, sum and average are supported; there is no PromQL parser.
- Samples are not persisted or scraped from targets.
- Alerts are written to a log, not to Alertmanager.

## License

Apache-2.0. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
