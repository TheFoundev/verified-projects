"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { deriveKey, encrypt, decrypt } = require("../src/crypto");

test("round trips a secret", () => {
  const key = deriveKey("master", "c2FsdC12YWx1ZQ==");
  const envelope = encrypt(key, "database-password");
  assert.equal(decrypt(key, envelope), "database-password");
});

test("wrong password fails to decrypt", () => {
  const good = deriveKey("master", "c2FsdC12YWx1ZQ==");
  const bad = deriveKey("other", "c2FsdC12YWx1ZQ==");
  const envelope = encrypt(good, "database-password");
  assert.throws(() => decrypt(bad, envelope));
});
