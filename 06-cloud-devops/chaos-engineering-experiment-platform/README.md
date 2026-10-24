# Chaos Engineering Experiment Platform

Define and run chaos experiments against a simulated service list.

> Design and run controlled failure experiments - pod kills, network latency, disk stress - on a Kubernetes-deployed application to validate resilience and error budgets.

## Overview

An original, self-contained implementation of the **Chaos Engineering Experiment Platform** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Experiment and action models with validation
- Delay, kill and stress actions
- Timeline of applied, recovered and rolled-back events with real timestamps
- Summary with the affected services and the total duration of the experiment
- A steady-state hypothesis that is checked before the first action and after
  every recovery; when it breaks the experiment stops and rolls back
- A `FaultExecutor` interface, so the same runner works against a dry run, a
  local process and Chaos Mesh
- A `Store` interface with an in-memory and a JSON file implementation, so the
  results of past experiments can be listed

## Stack

`Kubernetes; Go; Chaos Mesh; Prometheus; Helm`

The CSV stack is Kubernetes + Go + Chaos Mesh + Prometheus + Helm. This build runs the experiment engine against in-memory targets; Kubernetes, Chaos Mesh and Helm are not installed.

## Run

```bash
go run . -service checkout -action delay
go run . -service checkout -action delay -executor local   # really waits
go run . -service checkout -action delay -results runs.json # keeps the result
go test ./...
go test -race ./...
```

## Structure

```text
chaos-engineering-experiment-platform/
├── main.go                experiment CLI
├── chaos.go               validation, executors, metrics, stores and the runner
├── chaos_test.go          unit tests
└── go.mod
```

## Testing

```bash
go test ./...
```

16 tests: the validation, the hypothesis operators, a timeline with real
elapsed time, the total duration, the order of the actions, the steady-state
checks before and after, an experiment that is aborted because the steady
state was already broken, a failed apply and a failed recovery, the in-memory
and the file store, the dry-run executor, the local executor and the metric
reader.

## Notes and limitations

- No real pods are harmed. The `local` executor really waits and really
  signals a process id on the machine it runs on, but there is no Kubernetes
  or Chaos Mesh client yet.
- The metric reader is an interface with a static implementation, so there is
  no Prometheus scraping yet.
- Scheduling is immediate, not cron or event driven.

## License

Apache-2.0. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
