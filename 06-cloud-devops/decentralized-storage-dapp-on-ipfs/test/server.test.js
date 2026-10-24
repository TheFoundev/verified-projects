"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const sleepSync = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
function removeDirSync(dir) {
  // Windows can hold the SQLite handle for a few milliseconds after the child
  // process is terminated. Retry briefly instead of failing the test.
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      fs.rmSync(dir, { recursive: true, force: true });
      return;
    } catch (error) {
      if (attempt === 39) throw error;
      sleepSync(50);
    }
  }
}
const { createIpfsServer } = require("../src/server");
const { createStore } = require("../src/store");

async function withServer(run) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "server-"));
  const server = createIpfsServer(createStore(directory));
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    await run(base);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    removeDirSync(directory, { recursive: true, force: true });
  }
}

test("adds and fetches content through the api", async () => {
  await withServer(async (base) => {
    const add = await fetch(`${base}/api/add`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content: "stored content" }),
    });
    assert.equal(add.status, 201);
    const { cid } = await add.json();

    const fetched = await fetch(`${base}/api/cid/${cid}`);
    assert.equal(fetched.status, 200);
    assert.equal(await fetched.text(), "stored content");

    const missing = await fetch(`${base}/api/cid/${"b" + "a".repeat(58)}`);
    assert.equal(missing.status, 404);
  });
});

test("serves the dashboard", async () => {
  await withServer(async (base) => {
    const response = await fetch(`${base}/`);
    const body = await response.text();
    assert.equal(response.status, 200);
    assert.equal(body.includes("Content addressed store"), true);
  });
});
