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
const { createStore } = require("../src/store");

test("stores and returns content by cid", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "store-"));
  const store = createStore(directory);
  const cid = store.add("hello ipfs");
  assert.equal(store.has(cid), true);
  assert.equal(store.get(cid).toString("utf8"), "hello ipfs");
  assert.equal(store.list().length, 1);
  removeDirSync(directory, { recursive: true, force: true });
});

test("adding the same content returns the same cid", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "store-"));
  const store = createStore(directory);
  const first = store.add("same");
  const second = store.add("same");
  assert.equal(first, second);
  assert.equal(store.list().length, 1);
  removeDirSync(directory, { recursive: true, force: true });
});

test("unknown cid returns null", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "store-"));
  const store = createStore(directory);
  const cid = store.add("present");
  const missing = "b" + "a".repeat(58);
  assert.equal(store.get(missing), null);
  assert.equal(store.has(cid), true);
  removeDirSync(directory, { recursive: true, force: true });
});
