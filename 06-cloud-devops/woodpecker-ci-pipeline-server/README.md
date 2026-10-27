# Woodpecker CI Pipeline Server

Parse a pipeline file, trigger builds and track their status.

> Self-hosted CI/CD server that executes containerized build pipelines defined in YAML with webhook triggers, parallel stages and a web dashboard for a fleet of repos.

## Overview

An original, self-contained implementation of the **Woodpecker CI Pipeline Server** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Small YAML subset parser for pipeline steps
- Build records with status and logs
- Trigger and run endpoints
- In-memory build queue

## Stack

`Go`
The CSV stack is Go. This build keeps builds in memory and simulates step execution; there is no SQLite, OAuth provider or Docker runner.

## Run

```bash
go run .
go run . -listen :8084
go test ./...
```

## Structure

```text
woodpecker-ci-pipeline-server/
├── main.go                demo and server entry point
├── server.go              parser, builds and handlers
├── server_test.go         unit tests
└── go.mod
```

## Testing

```bash
go test ./...
```

4 tests (`go test ./...`), up from 2: the steps really run, a failing step stops the build and the runner on the machine.


## Notes and limitations

- Build logs are the recorded commands, not real output.
- There is no queue worker, retry or cancellation.
- OAuth and user accounts are not implemented.

## License

Apache-2.0. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
