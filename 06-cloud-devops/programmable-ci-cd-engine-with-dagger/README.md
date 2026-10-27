# Programmable CI/CD Engine with Dagger

Run a step graph with caching, like a small CI/CD engine.

> Define CI/CD pipelines as versioned code on a programmable engine so builds are reproducible locally and on any CI provider with caching and multi-platform image builds.

## Overview

An original, self-contained implementation of the **Programmable CI/CD Engine with Dagger** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Steps with images, commands and dependencies
- Topological ordering with cycle detection
- Content-hash cache for repeated steps
- Run report with logs and cache hits

## Stack

`Go; Dagger SDK; TypeScript; Docker; GraphQL`

The CSV stack is Go + Dagger SDK + TypeScript + Docker + GraphQL. This build runs the step graph on the host and simulates container steps; there is no Dagger engine, Docker daemon or GraphQL API.

## Run

```bash
go run .
go test ./...
```

## Structure

```text
programmable-ci-cd-engine-with-dagger/
├── main.go                pipeline demo
├── engine.go              ordering, cache and runner
├── engine_test.go         unit tests
└── go.mod
```

## Testing

```bash
go test ./...
go test -race ./...
```

18 tests: the order and the errors of the planner (an unknown dependency, a
cycle, a step that is defined twice), the cache (a second run is cached, a
change upstream invalidates the step below it, a step that did not change
stays cached), the steps really running (the command is executed, the output
is captured, the environment is set, a secret is not written to the log, a
failing step is retried, a step that times out fails), the file cache, the
dry run and the pipeline file.

## The engine

- `Order` puts the steps in dependency order and refuses an unknown
  dependency, a cycle and a step that is defined twice.
- `digestOf` hashes a step with the digests of the steps it needs, so a
  change upstream changes the key of everything below it. Hashing the names
  of the dependencies instead would serve a stale result after a change
  upstream, which is the bug this project had.
- The `Executor` interface runs a step: the `HostExecutor` really runs the
  command on the machine and captures the output, the `RecordingExecutor`
  only records what it would do.
- The `Cache` interface keeps the digests: `MemoryCache` in memory,
  `FileCache` in a directory, so the cache survives a restart.
- A step can have retries, a timeout, its own environment and a secret
  whose value never reaches the log.
- A failing step stops the pipeline and is reported with its exit code.

The command line has `-demo`, `-executor`, `-cache`, `-pipeline` and
`-workdir`.

## Notes and limitations

- Steps run as recorded commands, not real containers.
- The cache is keyed by step content and does not upload artifacts.
- There is no secrets store or remote runner.

## License

Apache-2.0. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
