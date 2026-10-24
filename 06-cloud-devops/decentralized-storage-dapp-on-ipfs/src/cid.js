"use strict";

const crypto = require("node:crypto");

const ALPHABET = "abcdefghijklmnopqrstuvwxyz234567";
const SHA256_MULTIHASH = 0x12;
const MULTIHASH_LENGTH = 32;
const RAW_CODEC = 0x55;
const CID_VERSION = 1;

function base32encode(buffer) {
  let bits = 0;
  let value = 0;
  let output = "";
  for (const byte of buffer) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      output += ALPHABET[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) {
    output += ALPHABET[(value << (5 - bits)) & 31];
  }
  return output;
}

function base32decode(text) {
  const clean = String(text).toLowerCase().replace(/=+$/, "");
  let bits = 0;
  let value = 0;
  const bytes = [];
  for (const character of clean) {
    const index = ALPHABET.indexOf(character);
    if (index === -1) {
      throw new Error("invalid base32 character");
    }
    value = (value << 5) | index;
    bits += 5;
    if (bits >= 8) {
      bytes.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(bytes);
}

function cidFromBytes(content) {
  const data = Buffer.isBuffer(content) ? content : Buffer.from(String(content), "utf8");
  const digest = crypto.createHash("sha256").update(data).digest();
  const multihash = Buffer.concat([Buffer.from([SHA256_MULTIHASH, MULTIHASH_LENGTH]), digest]);
  const cidBytes = Buffer.concat([Buffer.from([CID_VERSION, RAW_CODEC]), multihash]);
  return "b" + base32encode(cidBytes);
}

function decodeCid(cid) {
  const text = String(cid);
  if (!text.startsWith("b")) {
    throw new Error("only base32 cidv1 strings are supported");
  }
  const bytes = base32decode(text.slice(1));
  if (bytes.length !== 36) {
    throw new Error("unexpected cid length");
  }
  if (bytes[0] !== CID_VERSION || bytes[1] !== RAW_CODEC) {
    throw new Error("unsupported cid version or codec");
  }
  if (bytes[2] !== SHA256_MULTIHASH || bytes[3] !== MULTIHASH_LENGTH) {
    throw new Error("unsupported multihash");
  }
  return bytes.subarray(4, 36);
}

module.exports = { cidFromBytes, decodeCid, base32encode, base32decode };
