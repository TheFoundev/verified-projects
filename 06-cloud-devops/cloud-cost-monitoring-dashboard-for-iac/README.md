# Cloud Cost Monitoring Dashboard for IaC

Estimate infrastructure cost from a Terraform plan.

> Build a FinOps tool that parses Terraform plans, estimates monthly cloud costs per environment and posts diff comments on pull requests with budget alerts.

## Overview

An original, self-contained implementation of the **Cloud Cost Monitoring Dashboard for IaC** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Terraform plan JSON parsing
- Price table for common AWS resource types
- Totals by provider, type and resource
- JSON HTTP API for the estimate

## Stack

`Go`
The CSV stack is Go. This build parses a Terraform plan and exposes a JSON API; there is no database, React client or live cloud pricing feed.

## Run

```bash
go run .
go run . -listen :8080
go test ./...
```

## Structure

```text
cloud-cost-monitoring-dashboard-for-iac/
├── main.go                CLI and server entry point
├── cost.go                plan parsing and pricing
├── api.go                 JSON HTTP handler
├── cost_test.go           unit tests
└── go.mod
```

## Testing

```bash
go test ./...
```

7 tests (`go test ./...`), up from 2: the plan, the modules, the totals per type and per provider and the resources without a price.


## Notes and limitations

- Prices are a small hard-coded table, not the AWS Pricing API.
- Only a handful of AWS resource types are understood.
- There is no state history, budget alerting or per-tag allocation.

## License

Apache-2.0. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
