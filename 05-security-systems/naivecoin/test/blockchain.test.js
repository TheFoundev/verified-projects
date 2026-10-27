"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { Blockchain } = require("../src/blockchain");

test("genesis chain is valid", () => {
  const chain = new Blockchain(2);
  assert.equal(chain.chain.length, 1);
  assert.equal(chain.isValid(), true);
});

test("mining adds a valid block and clears pending transactions", () => {
  const chain = new Blockchain(2);
  chain.addTransaction("alice", "bob", 10);
  const block = chain.minePending("miner");
  assert.equal(chain.chain.length, 2);
  assert.equal(chain.pending.length, 0);
  assert.equal(block.hash().startsWith("00"), true);
  assert.equal(chain.isValid(), true);
});

test("balances include transfers and mining rewards", () => {
  const chain = new Blockchain(2);
  chain.addTransaction("alice", "bob", 10);
  chain.minePending("miner");
  assert.equal(chain.balance("alice"), -10);
  assert.equal(chain.balance("bob"), 10);
  assert.equal(chain.balance("miner"), 1);
});

test("tampering makes the chain invalid", () => {
  const chain = new Blockchain(2);
  chain.addTransaction("alice", "bob", 10);
  chain.minePending("miner");
  chain.chain[1].transactions[0].amount = 1000;
  assert.equal(chain.isValid(), false);
});

test("rejects invalid transactions", () => {
  const chain = new Blockchain(2);
  assert.throws(() => chain.addTransaction("", "bob", 10));
  assert.throws(() => chain.addTransaction("alice", "bob", 0));
  assert.throws(() => chain.addTransaction("alice", "bob", "lots"));
});
