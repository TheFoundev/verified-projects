# Kubernetes Control Plane for Cloud Resources

Reconcile desired cloud resources against stored state.

> Turn Kubernetes into a cloud control plane by composing databases, buckets and queues as declarative custom resources with claims, providers and composition policies.

## Overview

An original, self-contained implementation of the **Kubernetes Control Plane for Cloud Resources** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Custom resource documents with spec and status
- Create, update and delete planning
- Reconcile loop that applies the plan
- HTTP API for list, apply and status

## Stack

`Kubernetes; Go; Crossplane; Kubernetes API; YAML`

The CSV stack is Kubernetes + Go + Crossplane + Kubernetes API + YAML. This build implements a small control loop and JSON store instead of a Kubernetes API server or Crossplane provider.

## Run

```bash
go run . -demo                  # a short reconciliation, printed as JSON
go run . -serve -port 8080      # the HTTP API
go run . -serve -state state.json -interval 10s
go test ./...
```

Structure

```text
kubernetes-control-plane-for-cloud-resources/
├── main.go                demo reconciliation
├── control.go             resource store and planner
├── control_test.go        unit tests
└── go.mod
```

## The control plane

- `Plan` diffs the current resources against the desired ones and returns
  the actions: a create, an update or a delete. The desired spec travels with
  the action, so a reconciliation writes what was asked for.
- `Reconcile` applies the plan. A resource that cannot be written is
  reported and the other actions still go through.
- Every write goes up the `resourceVersion`, so a write that is based on an
  old read is refused instead of overwriting somebody else's change.
- A `SchemaRegistry` says what a kind needs: the fields that are required
  and the values a field may have. A desired state that does not pass the
  schema is refused.
- The `Controller` holds the desired state and reconciles the store with
  it, over and over.
- The `FileStore` keeps the resources in a JSON file, so a restart does not
  lose them.

The HTTP API answers `/api/resources`, `/api/desired`, `/api/plan`,
`/api/reconcile` and `/healthz`.

## Testing

```bash
# see the Run section
```

## Notes and limitations

- Resources live in memory; there is no persistence or admission webhook.
- No cloud provider is contacted; status is set by the reconciler.
- The API is a flat HTTP surface, not a Kubernetes-compatible endpoint.

## License

Apache-2.0. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
