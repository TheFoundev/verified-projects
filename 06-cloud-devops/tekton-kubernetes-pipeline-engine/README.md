# Tekton Kubernetes Pipeline Engine

Resolve pipeline references and execute tasks in dependency order.

> Kubernetes-native CI/CD engine that compiles pipeline custom resources into pods with shared workspaces, triggers and a dashboard for GitOps delivery.

## Overview

An original, self-contained implementation of the **Tekton Kubernetes Pipeline Engine** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Task and Pipeline definitions with params
- runAfter dependency resolution
- Validation of missing task references
- Execution status for every task

## Stack

`Kubernetes; Go; CRDs; kubectl; Docker`

The CSV stack is Kubernetes + Go + CRDs + kubectl. This build implements the resolver and executor locally; there is no Kubernetes API server, CRD installation or pod execution.

## Run

```bash
go run .
go test ./...
```

## Structure

```text
tekton-kubernetes-pipeline-engine/
├── main.go                pipeline demo
├── engine.go              resolution and execution
├── engine_test.go         unit tests
└── go.mod
```

## Testing

```bash
go test ./...
```

12 tests: the order of the tasks (and the errors for a missing reference, a
cycle and a task that is named twice), the parameters, the tasks really
running, a failing task stopping the pipeline, the finally tasks always
running, a step being retried, a step that times out, the results of a task
and the definitions being read from a directory.

## The engine

- `Resolve` puts the tasks in a topological order, honouring `runAfter`, and
  refuses a missing reference, a cycle and a task that is named twice.
- The parameters of a task are substituted in the commands, in the Tekton
  way: `$(params.name)`. A parameter that was not given is an error, because
  a command with a hole in it is worse than a command that does not run.
- The `Runner` runs the tasks for real: it executes the steps, captures the
  output, reports the exit code, measures the duration and reads the results
  the task wrote in the workspace. A step can have retries and a timeout, a
  failing task stops the pipeline and the tasks after it are skipped, and the
  tasks that are marked as finally always run.
- The tasks and the pipeline are read from a directory of manifests, and the
  tasks share a workspace, so one task can leave a file for the next.

The command line has `-pipeline`, `-definitions`, `-run` and `-workspace`.

## Notes and limitations

- Tasks run as recorded steps, not as Kubernetes pods.
- There is no retry, timeout or workspace handling.
- The definitions are plain Go structs, not YAML CRDs.

## License

Apache-2.0. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
