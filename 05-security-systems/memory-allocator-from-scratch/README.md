# Memory Allocator from Scratch

A malloc replacement built on mmap with a coalescing free list.

> A thread-safe malloc/free replacement in C that demonstrates free-list design and fragmentation handling.

## Overview

An original, self-contained implementation of the **Memory Allocator from Scratch** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Single mmap arena with 16-byte alignment
- First-fit allocation and block splitting
- Coalescing free list to limit fragmentation
- realloc, calloc and allocation statistics

## Stack

`C; POSIX`
The CSV stack is C + POSIX. This build implements the allocator with mmap and a free list; there is no thread-local cache or arena per thread.

## Run

```bash
make
./build/app
make test
```

## Structure

```text
memory-allocator-from-scratch/
├── src/allocator.c        mmap allocator implementation
├── src/allocator.h        public API
├── src/main.c             demo program
├── tests/test_main.c      unit tests
└── Makefile
```

## Testing

```bash
make test
```

5 tests (`make test`), up from 2: the allocation, the zeroed memory, the reallocation, the end of the arena and the free of nothing.


## Notes and limitations

- The arena is fixed at four megabytes and never grows.
- There is no locking, so it is not safe across threads.
- Fragmentation handling is a simple first fit with coalescing.

## License

MIT. See `LICENSE`.

## About the scope

This is a third-year student project. The build and tests run locally with GCC; there is no portability work beyond the platforms named in the notes.
