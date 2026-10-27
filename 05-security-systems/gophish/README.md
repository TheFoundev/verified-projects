# GoPhish

Plan a phishing-awareness campaign and score the results.

> An open-source phishing simulation platform used to run authorised awareness campaigns and staff training.

## Overview

An original, self-contained implementation of the **GoPhish** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Campaign and target models with unique tracking IDs
- Simulated send, open, click and report events
- Per-campaign statistics and rates
- JSON export for a training report

## Stack

`Go`
The CSV stack is Go. This build stores campaigns in memory and simulates delivery, so no mail server, database or tracking web server is required.

## Run

```bash
go run . -targets ana@example.com,bo@example.com
go test ./...
```

## Structure

```text
gophish/
├── main.go                campaign CLI
├── campaign.go            campaign and funnel logic
├── campaign_test.go       unit tests
└── go.mod
```

## Testing

```bash
go test ./...
```

5 tests: the tracking ids, the empty targets, the funnel, the rates and the
unknown tracking id.

## Notes and limitations

- No email is actually sent; events are recorded manually or from the demo.
- The tracking IDs are random strings, not signed tokens.
- Data is not persisted between runs.

## License

MIT. See `LICENSE`.

## About the scope

This is a third-year student project written in Go with the standard library only. It is not a hardened production service; the notes above list the missing pieces.
