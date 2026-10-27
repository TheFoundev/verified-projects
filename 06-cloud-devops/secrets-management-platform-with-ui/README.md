# Secrets Management Platform with UI

An encrypted secret store with an audit trail and a small web UI.

> Build a self-hosted secrets platform with project environments, role-based access, end-to-end encryption and integrations that sync secrets into apps and CI pipelines.

## Overview

An original, self-contained implementation of the **Secrets Management Platform with UI** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- AES-256-GCM encryption for every stored secret
- PBKDF2 key derivation from a master password
- Token protected JSON API for create, read and delete
- Append-only audit log and a simple management page

## Stack

`Node.js`
The CSV stack is Node.js.js + MongoDB + React. This build uses plain JavaScript, a JSON file instead of MongoDB and a vanilla HTML page instead of React, with only node:crypto and node:http.

## Run

```bash
SECRETS_PASSWORD=change-me npm start
npm test
```

## Structure

```text
secrets-management-platform-with-ui/
├── src/crypto.js           PBKDF2 and AES-GCM
├── src/store.js            encrypted file store and audit log
├── src/server.js           token protected API
├── public/                 management page
├── test/                   node:test suites
└── package.json
```

## Testing

```bash
# see the Run section
```

## Notes and limitations

- The master password comes from the environment; it is never stored.
- Secrets are held one file at a time, not encrypted field by field in a database.
- There is no key rotation, replication or access control beyond a single API token.

## License

MIT. See `LICENSE`.

## About the scope

This is a third-year student project. It targets Node's built-in modules and has no third-party runtime dependencies, so a few upstream conveniences are missing.
