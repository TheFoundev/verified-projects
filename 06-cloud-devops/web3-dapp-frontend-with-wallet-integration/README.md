# Web3 dApp Frontend with Wallet Integration

A wallet aware dapp front end built on raw EIP-1193 calls.

> Build a full-stack decentralized application that connects to wallets, reads and writes on-chain state, listens to contract events and renders transaction histories.

## Overview

An original, self-contained implementation of the **Web3 dApp Frontend with Wallet Integration** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Ethereum address validation helpers
- Decimal unit formatting and parsing for wei values
- ABI encoding for an ERC-20 transfer call
- Browser page that talks to an injected wallet

## Stack

`Node.js`
The CSV stack is Node.js.js + React + TypeScript + MetaMask + Vite. There are no third-party packages here, so the page is plain HTML/JavaScript that calls window.ethereum directly instead of using ethers and React.

## Run

```bash
npm start
npm test
```

## Structure

```text
web3-dapp-frontend-with-wallet-integration/
├── src/ethereum.js         address and ABI helpers
├── src/server.js           static file server
├── public/                 wallet aware page
├── test/                   node:test suites
└── package.json
```

## Testing

```bash
# see the Run section
```

## Notes and limitations

- No EIP-55 checksum checking: keccak-256 is not available without a library.
- Only a handful of wallet calls are wrapped, not the full ethers API.
- The page needs an injected wallet such as MetaMask to send a real transaction.

## License

MIT. See `LICENSE`.

## About the scope

This is a third-year student project. It targets Node's built-in modules and has no third-party runtime dependencies, so a few upstream conveniences are missing.
