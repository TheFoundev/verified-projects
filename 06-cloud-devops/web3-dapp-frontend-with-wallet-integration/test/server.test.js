"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { createDappServer } = require("../src/server");

test("serves the page and the info endpoint", async () => {
  const server = createDappServer();
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const page = await fetch(`${base}/`);
    assert.equal(page.status, 200);
    assert.equal((await page.text()).includes("Wallet dapp demo"), true);

    const info = await fetch(`${base}/api/info`);
    const body = await info.json();
    assert.equal(body.transferSelector, "0xa9059cbb");

    const missing = await fetch(`${base}/nope`);
    assert.equal(missing.status, 404);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});
