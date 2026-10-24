# Decentralized Storage dApp on IPFS

Content-addressed file storage with real CIDv1 identifiers.

> Create a decentralized file-sharing application where uploads are content-addressed on IPFS, access is recorded on-chain and files are retrieved through gateways.

## Overview

An original, self-contained implementation of the **Decentralized Storage dApp on IPFS** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- CIDv1 built from a SHA-256 multihash with the raw codec
- RFC 4648 base32 encoding exactly like an IPFS cidv1 string
- Local content-addressed store with integrity checking
- Web page and JSON API for adding and fetching content

## Stack

`Node.js`
The CSV stack is Node.js.js. There is no IPFS daemon here, so the store is local and the page is plain HTML instead of React; the CID computation follows the same format.

## Run

```bash
npm start
npm test
```

## Structure

```text
decentralized-storage-dapp-on-ipfs/
├── src/cid.js              CIDv1 and base32 helpers
├── src/store.js            local content addressed store
├── src/server.js           JSON API and static page
├── public/                 browser page
├── test/                   node:test suites
└── package.json
```

## Testing

```bash
npm test
```

15 tests: the CID, the store, the server and the client of an IPFS node
(put, read, pin, the pins of the node and the errors).


## Notes and limitations

- Content is not replicated, announced or announced to any network.
- Only the raw codec (0x55) is produced; dag-pb and other codecs are out of scope.
- There is no pinning, quota or garbage collection.

## License

Apache-2.0. See `LICENSE`.

## About the scope

This is a third-year student project. It targets Node's built-in modules and has no third-party runtime dependencies, so a few upstream conveniences are missing.
