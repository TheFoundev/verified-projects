"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { acceptKey, encodeText, parseFrame } = require("../src/ws");

function maskedFrame(text) {
  const payload = Buffer.from(text, "utf8");
  const mask = Buffer.from([1, 2, 3, 4]);
  const masked = Buffer.from(payload.map((byte, index) => byte ^ mask[index % 4]));
  return Buffer.concat([Buffer.from([0x81, 0x80 | payload.length]), mask, masked]);
}

test("computes the RFC 6455 accept key", () => {
  assert.equal(acceptKey("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=");
});

test("parses a masked client frame", () => {
  const frame = parseFrame(maskedFrame("hello"));
  assert.equal(frame.opcode, 0x1);
  assert.equal(frame.payload.toString("utf8"), "hello");
  assert.equal(frame.rest.length, 0);
});

test("returns null for a partial frame", () => {
  const frame = maskedFrame("hello");
  assert.equal(parseFrame(frame.subarray(0, 4)), null);
});

test("encodes a server text frame", () => {
  const frame = encodeText("hi");
  assert.equal(frame[0], 0x81);
  assert.equal(frame[1], 2);
  assert.equal(frame.subarray(2).toString("utf8"), "hi");
});

test("a client masks its frames", () => {
  const { encodeFrame, parseFrame } = require("../src/ws");
  const frame = encodeFrame(0x1, Buffer.from("hi"), true);
  // The mask bit is set.
  assert.strictEqual(frame[1] & 0x80, 0x80);
  const parsed = parseFrame(frame);
  assert.strictEqual(parsed.payload.toString(), "hi");
});

test("a frame that is too large is refused", () => {
  const { parseFrame } = require("../src/ws");
  const header = Buffer.alloc(10);
  header[0] = 0x81;
  header[1] = 127;
  header.writeBigUInt64BE(BigInt(10 * 1024 * 1024), 2);
  assert.throws(() => parseFrame(header), /too large/);
});

test("a client frame without a mask is refused by the server", () => {
  const { encodeFrame, parseFrame } = require("../src/ws");
  const frame = encodeFrame(0x1, Buffer.from("hi"), false);
  assert.throws(() => parseFrame(frame, { requireMask: true }), /mask/);
});

test("the fragments of a message are put together", () => {
  const { encodeFrame, parseFrames } = require("../src/ws");
  const first = encodeFrame(0x1, Buffer.from("he"), true);
  const rest = encodeFrame(0x0, Buffer.from("llo"), true);
  const { frames } = parseFrames(Buffer.concat([first, rest]), { requireMask: true });
  assert.strictEqual(frames.length, 1);
  assert.strictEqual(frames[0].payload.toString(), "hello");
});
