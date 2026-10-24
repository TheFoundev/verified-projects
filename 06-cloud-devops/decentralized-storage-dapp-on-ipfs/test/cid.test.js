"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const { cidFromBytes, decodeCid, base32encode, base32decode } = require("../src/cid");

test("base32 round trips", () => {
  const data = Buffer.from("ipfs content addressing");
  const encoded = base32encode(data);
  assert.equal(base32decode(encoded).equals(data), true);
});

test("cid is deterministic and content dependent", () => {
  const first = cidFromBytes("hello");
  const second = cidFromBytes("hello");
  const third = cidFromBytes("hello world");
  assert.equal(first, second);
  assert.notEqual(first, third);
  assert.equal(first.startsWith("b"), true);
  assert.equal(first.length, 59);
});

test("decoded digest matches sha256", () => {
  const content = Buffer.from("block payload");
  const cid = cidFromBytes(content);
  const digest = decodeCid(cid);
  const expected = crypto.createHash("sha256").update(content).digest();
  assert.equal(digest.equals(expected), true);
});

test("rejects malformed cids", () => {
  assert.throws(() => decodeCid("zabc"));
  assert.throws(() => decodeCid("b" + base32encode(Buffer.alloc(4))));
});
