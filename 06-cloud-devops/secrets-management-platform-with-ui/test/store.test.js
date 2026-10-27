"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { SecretStore } = require("../src/store");

function tempFile() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "secrets-"));
  return path.join(directory, "secrets.json");
}

test("stores, lists and reads secrets", () => {
  const file = tempFile();
  const store = new SecretStore(file, "master");
  store.set("api/key", "sk-live-123");
  store.set("db/password", "hunter2");
  assert.equal(store.get("api/key"), "sk-live-123");
  assert.equal(store.get("missing"), null);
  assert.deepEqual(store.list().map((entry) => entry.name), ["api/key", "db/password"]);
  assert.equal(store.audit().length, 2);
});

test("persists encrypted data across reloads", () => {
  const file = tempFile();
  const first = new SecretStore(file, "master");
  first.set("api/key", "sk-live-456");
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  assert.equal(JSON.stringify(raw).includes("sk-live-456"), false);

  const second = new SecretStore(file, "master");
  assert.equal(second.get("api/key"), "sk-live-456");
});

test("rejects empty names and values", () => {
  const store = new SecretStore(tempFile(), "master");
  assert.throws(() => store.set("", "value"));
  assert.throws(() => store.set("name", ""));
});

test("deletes secrets and records it", () => {
  const store = new SecretStore(tempFile(), "master");
  store.set("temp", "value");
  assert.equal(store.remove("temp"), true);
  assert.equal(store.get("temp"), null);
  assert.equal(store.remove("temp"), false);
  assert.equal(store.audit().some((entry) => entry.action === "delete"), true);
});
