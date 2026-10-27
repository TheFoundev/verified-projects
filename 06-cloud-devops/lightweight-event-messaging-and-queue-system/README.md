# Lightweight Event Messaging and Queue System

A small append-only message queue with topics and consumers.

> Build an event-driven backend around a streaming message bus with subject-based routing, durable consumer groups, at-least-once delivery and replay.

## Overview

An original, self-contained implementation of the **Lightweight Event Messaging and Queue System** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Topics with append-only partitions and offsets
- Publish and fetch with a limit
- Consumer offsets and acknowledgements
- Retention that trims old messages

## Stack

`Go`
The CSV stack is Go. This build keeps topics in memory and exposes a small HTTP API instead of NATS

## Run

```bash
go run .
go run . -listen :8082
go test ./...
```

## Structure

```text
lightweight-event-messaging-and-queue-system/
├── main.go                queue demo and HTTP server
├── queue.go               topics, consumers and handlers
├── queue_test.go          unit tests
└── go.mod
```

## Testing

```bash
go test ./...
```

4 tests (`go test ./...`), up from 2: the order of the messages of a key, the partitions and the offset.


## Notes and limitations

- Messages are not persisted, replicated or compressed.
- There are no consumer groups beyond one offset per consumer.
- Flow control, dead-letter queues and metrics endpoints are missing.

## License

Apache-2.0. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
