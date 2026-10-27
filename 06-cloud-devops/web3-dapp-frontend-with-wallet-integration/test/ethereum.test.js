"use strict";

const test = require("node:test");
const assert = require("node:assert");

const {
  isAddress,
  hasValidChecksum,
  normalizeAddress,
  formatUnits,
  parseUnits,
  encodeTransfer,
} = require("../src/ethereum");
const { keccak256 } = require("../src/keccak");

test("the keccak digest of the empty text", () => {
  assert.equal(
    keccak256(Buffer.from("")),
    "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
  );
});

test("the keccak digest of abc", () => {
  assert.equal(
    keccak256(Buffer.from("abc")),
    "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
  );
});

test("an address with the checksum of EIP-55 is accepted", () => {
  const addresses = [
    "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
    "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
  ];
  for (const address of addresses) {
    assert.ok(hasValidChecksum(address), address);
  }
});

test("an address with a wrong checksum is refused", () => {
  assert.equal(hasValidChecksum("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD"), false);
});

test("an address without a checksum is accepted", () => {
  assert.ok(hasValidChecksum("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"));
  assert.ok(hasValidChecksum("0x5AAEB6053F3E94C9B9A09F33669435E7EF1BEAED"));
});

test("an address that is not an address is refused", () => {
  assert.equal(isAddress("hello"), false);
  assert.equal(isAddress("0x123"), false);
  assert.equal(isAddress(42), false);
  assert.throws(() => normalizeAddress("nope"));
});

test("an address with a wrong checksum cannot be used", () => {
  assert.throws(
    () => normalizeAddress("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD"),
    /checksum/,
  );
});

test("the units are formatted and read back", () => {
  assert.equal(formatUnits("1000000000000000000"), "1");
  assert.equal(formatUnits("1500000000000000000"), "1.5");
  assert.equal(formatUnits("1", 6), "0.000001");
  assert.equal(parseUnits("1.5"), "1500000000000000000");
  assert.equal(parseUnits("0.000001", 6), "1");
});

test("a negative amount cannot be parsed", () => {
  assert.throws(() => parseUnits("-1"));
  assert.throws(() => parseUnits("1.2.3"));
});

test("the transfer is encoded the way the contracts want it", () => {
  const encoded = encodeTransfer("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed", "1000");
  assert.ok(encoded.startsWith("0xa9059cbb"));
  assert.equal(encoded.length, 2 + 8 + 64 + 64);
  assert.equal(encoded.slice(74), (1000).toString(16).padStart(64, "0"));
});
