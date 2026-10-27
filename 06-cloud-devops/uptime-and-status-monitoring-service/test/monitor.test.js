"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const { checkOnce, runOnce } = require("../src/monitor");
const { openDatabase } = require("../src/db");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

async function withTarget(handler, run) {
  const server = http.createServer(handler);
  await new Promise((resolve) => server.listen(0, resolve));
  const url = `http://127.0.0.1:${server.address().port}/`;
  try {
    await run(url);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
}

test("reports a healthy endpoint as up", async () => {
  await withTarget((request, response) => {
    response.writeHead(200, { "content-type": "text/plain" });
    response.end("ok");
  }, async (url) => {
    const result = await checkOnce({ url });
    assert.equal(result.status, "up");
    assert.equal(result.code, 200);
    assert.equal(result.latencyMs >= 0, true);
  });
});

test("reports a failing endpoint as down", async () => {
  await withTarget((request, response) => {
    response.writeHead(503);
    response.end("nope");
  }, async (url) => {
    const result = await checkOnce({ url });
    assert.equal(result.status, "down");
    assert.equal(result.code, 503);
  });
});

test("reports an unreachable endpoint as down", async () => {
  const result = await checkOnce({ url: "http://127.0.0.1:1/" });
  assert.equal(result.status, "down");
  assert.equal(result.code, null);
});

test("runOnce stores results for every monitor", async () => {
  const store = openDatabase(path.join(fs.mkdtempSync(path.join(os.tmpdir(), "status-")), "status.db"));
  store.createMonitor({ name: "target", url: "http://127.0.0.1:1/" });
  const { results } = await runOnce(store, undefined, { log: false });
  assert.equal(results.length, 1);
  assert.equal(results[0].result.status, "down");
  assert.equal(store.recentChecks(results[0].monitor.id).length, 1);
  store.close();
});

test("a monitor that goes down is announced", async () => {
  const { notificationsFor } = require("../src/monitor");
  const { openDatabase } = require("../src/db");
  const store = openDatabase(":memory:");
  try {
    const monitor = store.createMonitor({ name: "site", url: "http://127.0.0.1:1" });
    const first = notificationsFor(store, monitor, { status: "down", message: "connection refused" });
    assert.equal(first.length, 1);
    assert.match(first[0].message, /is down/);
  } finally {
    store.close();
  }
});

test("a monitor that stays down is not announced again", async () => {
  const { notificationsFor } = require("../src/monitor");
  const { openDatabase } = require("../src/db");
  const store = openDatabase(":memory:");
  try {
    const monitor = store.createMonitor({ name: "site", url: "http://127.0.0.1:1" });
    store.recordCheck({ monitorId: monitor.id, status: "down", message: "connection refused" });

    const again = notificationsFor(store, monitor, { status: "down", message: "connection refused" });
    assert.equal(again.length, 0);
  } finally {
    store.close();
  }
});

test("a monitor that comes back is announced", async () => {
  const { notificationsFor } = require("../src/monitor");
  const { openDatabase } = require("../src/db");
  const store = openDatabase(":memory:");
  try {
    const monitor = store.createMonitor({ name: "site", url: "http://127.0.0.1:1" });
    store.recordCheck({ monitorId: monitor.id, status: "down", message: "connection refused" });

    const back = notificationsFor(store, monitor, { status: "up", message: "" });
    assert.equal(back.length, 1);
    assert.match(back[0].message, /is up again/);
  } finally {
    store.close();
  }
});

test("the webhook is called with the notification", async () => {
  const { notify } = require("../src/monitor");
  const sent = [];
  const answer = await notify(
    { monitor: { name: "site" }, status: "down", message: "site is down" },
    {
      webhook: "http://127.0.0.1:9/hook",
      send: async (url, options) => {
        sent.push({ url, body: JSON.parse(options.body) });
        return { ok: true };
      },
      log: false,
    },
  );
  assert.equal(answer.sent, true);
  assert.equal(sent.length, 1);
  assert.match(sent[0].body.message, /is down/);
});
