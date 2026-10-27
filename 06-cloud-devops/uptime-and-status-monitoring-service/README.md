# Uptime and Status Monitoring Service

A small uptime monitor with a status dashboard.

> Develop a self-hosted status-page service that monitors HTTP, TCP, DNS and heartbeat endpoints with real-time latency charts, incident history and public status pages.

## Overview

An original, self-contained implementation of the **Uptime and Status Monitoring Service** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- SQLite storage for monitors and their check history
- HTTP checks with timeout, status code and latency
- Scheduler that re-checks every monitor on an interval
- JSON API and a polling status page

## Stack

`Node.js`
The CSV stack is Node.js.js + Vue + SQLite + Docker + WebSockets. This build uses the built-in node:sqlite module and a plain HTML dashboard that polls the API instead of a Vue single page app.

## Run

```bash
npm start
npm test
```

## Structure

```text
uptime-and-status-monitoring-service/
├── src/db.js               SQLite monitors and checks
├── src/monitor.js          HTTP checks and scheduler
├── src/server.js           JSON API
├── public/                 status dashboard
├── test/                   node:test suites
└── package.json
```

## Testing

```bash
npm test
```

13 tests: the checks, the schedule, the notifications (a monitor that goes down
is announced, one that stays down is not, one that comes back is) and the
webhook.


## Notes and limitations

- Checks are plain HTTP GET requests; there is no TCP, TLS expiry or content matching.
- The dashboard polls rather than pushing over WebSockets.
- Notifications, alerting and multi-node deployment are out of scope.

## License

MIT. See `LICENSE`.

## About the scope

This is a third-year student project. It targets Node's built-in modules and has no third-party runtime dependencies, so a few upstream conveniences are missing.
