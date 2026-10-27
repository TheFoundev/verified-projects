# Naivecoin

A small proof-of-work blockchain with a peer relay.

> A minimal cryptocurrency implementation with a block explorer UI for learning blockchain internals safely.

## Overview

An original, self-contained implementation of the **Naivecoin** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Transactions and blocks hashed with SHA-256
- Proof-of-work mining with a configurable difficulty
- Chain validation that detects tampering
- Wallet balances derived from the chain
- HTTP API and a hand-rolled WebSocket peer relay

## Stack

`Node.js`
The CSV stack is Node.js.js + WebSockets. Node has no built-in WebSocket server, so the project includes a small RFC 6455 handshake and frame parser instead of the ws package.

## Run

```bash
npm start
node src/main.js
npm test
```

## Structure

```text
naivecoin/
├── src/blockchain.js       blocks, transactions and validation
├── src/ws.js               minimal WebSocket server
├── src/node.js             HTTP API and peer relay
├── src/server.js           server entry point
├── src/main.js             local chain demo
├── test/                   node:test suites
└── package.json
```

## Testing

```bash
# see the Run section
```

## Notes and limitations

- There is no consensus fork choice, peer discovery or persistence to disk.
- Transactions are not signed and there is no UTXO model.
- The WebSocket layer is minimal: text frames, ping/pong and close only.

## License

Apache-2.0. See `LICENSE`.

## About the scope

This is a third-year student project. It targets Node's built-in modules and has no third-party runtime dependencies, so a few upstream conveniences are missing.
