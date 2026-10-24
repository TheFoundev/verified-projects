# Audited Escrow and Tokenization Contracts

> A secure multi-party escrow with dispute resolution and a tokenized asset contract, composed from audited OpenZeppelin primitives instead of rolling your own cryptography.

## Overview

An original, self-contained implementation of escrow + tokenization contracts built for the Foundev portfolio. Every security-critical building block (reentrancy guard, access control, pausability, safe ERC20 transfers, the ERC721 token itself) comes from OpenZeppelin's audited library; this project composes them into an explicit escrow state machine and an asset token with per-token provenance, and proves the behaviour with a Hardhat + TypeScript test suite.

## Features

- Multi-party escrow between buyer, seller and an arbiter, settled in native ETH or a single ERC20
- Explicit state machine (`Created → Funded → Released / Disputed / Refunded / ArbiterDecided`) with custom errors and full event coverage
- Delivery-window dispute raising, two-phase arbiter ruling, and deadline-based timeout release (seller) / timeout refund (buyer) when a party or the arbiter goes silent
- Pull-payment withdrawals only — no direct sends anywhere; checks-effects-interactions plus OpenZeppelin `ReentrancyGuard`
- `TokenizedAsset`: ERC721 with `AccessControl` (minter / auditor roles), `Pausable` incident response, per-token provenance (original creator, escrow link, audit verdict) and a SafeERC20 royalty pool
- `EscrowFactory` that deploys escrow instances and keeps an on-chain queryable index (by creation order, paginated, by buyer, by seller)
- A test-only reentrancy attacker contract that proves `withdraw` cannot be re-entered

## Stack

`Solidity; OpenZeppelin; Hardhat; TypeScript; ethers.js`

## Architecture

Escrow state machine (all value leaves the contract only through `withdraw`):

```text
                    fund() / fundERC20() by buyer        after deliveryDeadline:
                    (partial contributions OK)           anyone calls timeoutRelease()
                 ┌───────────────────────────────┐      ┌────────────────────┐
                 │                               ▼      ▼                    │
        ┌─────────┐  reaches `amount`       ┌────────┐                       │
        │ Created │ ───────────────────────▶│ Funded │── release() ─────────▶│
        └────┬────┘                         └───┬────┘   (buyer, any time)  │
             │ cancel()                         │                            │
             │ (buyer or seller;                │ refund()                   │
             │  partial amount                  │ (seller, voluntary)        │
             │  returned to buyer)              │                            │
             ▼                                 │ raiseDispute()              │
        ┌──────────┐                           │ (buyer/seller, before       │
        │ Refunded │◀──────────────────────────┤  deliveryDeadline)          │
        │(terminal)│   timeoutRefund()         ▼                             │
        └──────────┘   (after dispute          ┌──────────┐    ▲             │
             ▲         deadline)               │ Disputed │────┘            │
             │                                 └────┬─────┘                 │
             │            arbiterRule(true|false)   │                        │
             │            by arbiter, in window     ▼                        │
             │                           ┌─────────────────┐                 │
             │                           │ ArbiterDecided  │                 │
             │                           └────────┬────────┘                 │
             │              executeRuling()       │                          │
             │         ┌──────────────────────────┴───────────────┐          │
             │         ▼                                          ▼          │
             │   ┌──────────┐                                ┌──────────┐    │
             └───┤ Refunded │                                │ Released │◀───┘
                 │(terminal)│                                │(terminal)│
                 └──────────┘                                └──────────┘
                   credit buyer                                credit seller
                        │                                            │
                        └────────────── withdraw() ──────────────────┘
                              (pull payment, CEI, ReentrancyGuard)
```

Roles on `TokenizedAsset`: `DEFAULT_ADMIN_ROLE` grants/revokes, `MINTER_ROLE` mints with provenance, `AUDITOR_ROLE` records audit verdicts and pauses/unpauses.

## Run

```bash
npm install
npm run compile
npm test
npm run deploy -- --network localhost   # with `npx hardhat node` running in another terminal
```

Environment variables (RPC endpoints, deployer key) are documented in `.env.example`. The `PRIVATE_KEY` value there is a placeholder of 64 zeros — never a real key; export your real key in the shell, never commit it.

## Structure

```text
audited-escrow-and-tokenization-contracts/
├── contracts/
│   ├── Escrow.sol                    multi-party escrow with dispute resolution
│   ├── TokenizedAsset.sol            ERC721 asset: provenance, roles, pause, royalties
│   ├── EscrowFactory.sol             deploys escrows and indexes them on-chain
│   └── test/
│       ├── MockERC20.sol             test token (ERC20 settlement + royalty pool)
│       └── ReentrancyAttacker.sol    re-entrancy attacker used by the tests
├── test/
│   ├── escrow.spec.ts                state machine, disputes, timeouts, ERC20, re-entrancy
│   ├── tokenized-asset.spec.ts       provenance, roles, pausing, royalty pool
│   └── factory.spec.ts               create, event, pagination, party queries
├── scripts/deploy.ts                 deploys TokenizedAsset + EscrowFactory
├── hardhat.config.ts                 solc 0.8.24 (evm target cancun), env-driven networks
├── tsconfig.json
├── package.json                      scripts: compile, test, deploy
├── .env.example                      RPC URLs + placeholder PRIVATE_KEY
├── .gitignore
├── LICENSE                           MIT
└── README.md
```

## Verification (what was actually run)

All of the following were executed in this environment (Node v26.10.0, npm 12.1.0, npm registry reachable) and passed:

| Command | Result |
| --- | --- |
| `npm install` | hardhat 2.29.1, @openzeppelin/contracts 5.6.1, @nomicfoundation/hardhat-toolbox 5.0.0, ethers 6.17.0, typescript 5.9.3 |
| `npx hardhat compile` | compiled all contracts (32 sources incl. OpenZeppelin), evm target cancun |
| `npx hardhat test` | **37 passing, 0 failing** |
| `npx tsc --noEmit` | no type errors |
| `npm run deploy` | printed `TokenizedAsset` and `EscrowFactory` addresses |

Hardhat downloaded the solc 0.8.24 binary successfully here, so the fallback standalone-`solc` compile gate was not needed; the shipped test suite is fully runnable in any environment where hardhat can reach the solc download host. OpenZeppelin 5.6 uses the Cancun `mcopy` opcode, which is why the config sets `evmVersion: "cancun"`.

## Security considerations

- **Checks-effects-interactions.** Every state write (credits, state transitions) happens before any external call. `withdraw` zeroes the caller's credit before transferring.
- **Pull payments only.** `release`, `refund`, `timeoutRelease`, `timeoutRefund` and `executeRuling` never send value — they only book internal `credits`. Value leaves exclusively through `withdraw`, so a hostile or gas-guzzling receiver cannot block settlement for anyone else.
- **Reentrancy.** `withdraw`, `fund` and `fundERC20` are wrapped in OpenZeppelin's `ReentrancyGuard`; `withdraw` additionally follows CEI, so it has two independent defences. The test suite deploys `contracts/test/ReentrancyAttacker.sol` and proves the re-entrant call fails while the legitimate payout succeeds exactly once. Direct ETH sends to the escrow are rejected outright (`receive` reverts).
- **Dispute deadlines.** The delivery window bounds how long a seller can be blocked by a silent buyer (after it, `timeoutRelease` pays the seller). The dispute window bounds arbiter power (after it, `arbiterRule` reverts and `timeoutRefund` pays the buyer). Deadlines are absolute timestamps set on-chain at funding/dispute time — no party controls the clock.
- **Composition over invention.** Reentrancy guard, access control, pausing, safe ERC20 transfers and ERC721 are OpenZeppelin primitives, not hand-rolled cryptography.
- **This is a demo, get audits before real funds.** Nothing here has been audited by a third party. Do not deploy it with real money without an independent audit, extensive fuzzing/invariant testing, and a review of the threat model for your specific use case.

## Limitations

- Single trusted arbiter per escrow; no multi-sig arbiter, appeal process or arbitration DAO.
- One settlement asset per escrow instance (chosen at creation); no partial multi-currency funding.
- No protocol fees, no escrow cancellation after funding by mutual consent (only the dispute path), no upgradeability.
- Royalties are a simple SafeERC20 pull-payment pool plus provenance — no ERC2981 signalling or marketplace integration.
- `EscrowFactory`'s index is append-only; escrows are never archived or removed.
- The auditor role is a single address by default; production would use a multisig or a role-permissioned committee.
- Tests cover the paths listed above but there is no fuzzing, invariant testing or formal verification in this repository.

## License

MIT. See `LICENSE`.
