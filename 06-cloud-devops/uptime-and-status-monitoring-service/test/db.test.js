"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { openDatabase } = require("../src/db");

function tempDb() {
  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), "status-")), "status.db");
}

test("creates, lists and deletes monitors", () => {
  const store = openDatabase(tempDb());
  const monitor = store.createMonitor({ name: "home", url: "http://localhost:1/", intervalSeconds: 30 });
  assert.equal(typeof monitor.id, "number");
  assert.equal(store.listMonitors().length, 1);
  assert.equal(store.deleteMonitor(monitor.id), true);
  assert.equal(store.deleteMonitor(monitor.id), false);
  assert.equal(store.listMonitors().length, 0);
  store.close();
});

test("records checks and summarises status", () => {
  const store = openDatabase(tempDb());
  const monitor = store.createMonitor({ name: "api", url: "http://localhost:2/" });
  assert.equal(store.summary()[0].status, "unknown");
  store.recordCheck({ monitorId: monitor.id, status: "up", code: 200, latencyMs: 12, message: "" });
  store.recordCheck({ monitorId: monitor.id, status: "down", code: null, latencyMs: 5000, message: "timeout" });
  const [summary] = store.summary();
  assert.equal(summary.status, "down");
  assert.equal(summary.latencyMs, 5000);
  assert.equal(store.recentChecks(monitor.id).length, 2);
  store.close();
});

test("requires name and url", () => {
  const store = openDatabase(tempDb());
  assert.throws(() => store.createMonitor({ name: "", url: "" }));
  store.close();
});
