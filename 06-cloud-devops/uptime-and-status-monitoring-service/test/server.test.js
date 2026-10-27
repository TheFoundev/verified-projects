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
const { createStatusServer } = require("../src/server");
const { openDatabase } = require("../src/db");

async function withServer(run) {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "status-api-")), "status.db");
  const store = openDatabase(file);
  const server = createStatusServer(store);
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    await run(base);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    store.close();
    removeDirSync(path.dirname(file), { recursive: true, force: true });
  }
}

test("creates a monitor and runs a check", async () => {
  await withServer(async (base) => {
    const created = await fetch(`${base}/api/monitors`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: "example", url: "http://127.0.0.1:1/", intervalSeconds: 30 }),
    });
    assert.equal(created.status, 201);
    const { monitor } = await created.json();

    const check = await fetch(`${base}/api/monitors/${monitor.id}/check`, { method: "POST" });
    const checkBody = await check.json();
    assert.equal(check.status, 200);
    assert.equal(checkBody.result.status, "down");

    const checks = await fetch(`${base}/api/monitors/${monitor.id}/checks`);
    assert.equal((await checks.json()).checks.length, 1);

    const listed = await fetch(`${base}/api/monitors`);
    assert.equal((await listed.json()).monitors[0].status, "down");

    const removed = await fetch(`${base}/api/monitors/${monitor.id}`, { method: "DELETE" });
    assert.equal(removed.status, 200);

    const missing = await fetch(`${base}/api/monitors/${monitor.id}`, { method: "DELETE" });
    assert.equal(missing.status, 404);
  });
});

test("serves the dashboard page", async () => {
  await withServer(async (base) => {
    const response = await fetch(`${base}/`);
    const body = await response.text();
    assert.equal(response.status, 200);
    assert.equal(body.includes("Status"), true);
  });
});
