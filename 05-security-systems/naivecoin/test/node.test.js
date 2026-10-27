"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { createNodeServer } = require("../src/node");
const { Blockchain } = require("../src/blockchain");

test("http api exposes, mines and validates blocks", async () => {
  const server = createNodeServer(new Blockchain(2));
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    let response = await fetch(`${base}/api/blocks`);
    assert.equal(response.status, 200);
    let blocks = await response.json();
    assert.equal(blocks.length, 1);

    response = await fetch(`${base}/api/transactions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ from: "alice", to: "bob", amount: 5 }),
    });
    assert.equal(response.status, 201);

    response = await fetch(`${base}/api/mine`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ miner: "miner" }),
    });
    assert.equal(response.status, 200);

    response = await fetch(`${base}/api/blocks`);
    blocks = await response.json();
    assert.equal(blocks.length, 2);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});
