# Encrypted Deduplicated Backup System

Chunk, deduplicate and encrypt backups before restoring them.

> Design an automated backup architecture with client-side encryption, content-defined chunking deduplication, incremental snapshots and restore drills to off-site object storage.

## Overview

An original, self-contained implementation of the **Encrypted Deduplicated Backup System** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Content-defined chunking with a rolling hash
- Deduplication by chunk hash
- AES-GCM encryption for every stored chunk
- Manifest that records the chunk list per file

## Stack

`Go`
The CSV stack is Go. This build implements chunking, dedup and encryption locally; there is no S3 backend, scheduler or compression.

## Run

```bash
go run . -source README.md -repo artifacts/repository
go test ./...
```

## Structure

```text
encrypted-deduplicated-backup-system/
├── main.go                backup and restore CLI
├── repository.go          chunking, dedup and encryption
├── repository_test.go     unit tests
└── go.mod
```

## Testing

```bash
# see the Run section
```

## Notes and limitations

- Chunks are stored as individual encrypted files, not pack files.
- There is no incremental snapshot bookkeeping or prune command.
- The repository is a local directory; uploads to object storage are not implemented.

## License

BSD-3-Clause. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
