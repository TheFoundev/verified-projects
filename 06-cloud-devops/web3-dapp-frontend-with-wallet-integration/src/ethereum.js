"use strict";

const { keccak256 } = require("./keccak");

const TRANSFER_SELECTOR = "a9059cbb";
const ADDRESS_LENGTH = 40;

function isAddress(value) {
  return typeof value === "string" && /^0x[0-9a-fA-F]{40}$/.test(value);
}

/**
 * True when the address has the checksum of EIP-55.
 *
 * The checksum is a mixed case address, and the case of every letter says
 * whether the address was written down correctly. An address that is all
 * lower case or all upper case has no checksum and is accepted as well.
 */
function hasValidChecksum(value) {
  const body = value.slice(2);
  if (body === body.toLowerCase() || body === body.toUpperCase()) {
    return true;
  }
  const hash = keccak256(body.toLowerCase());
  for (let index = 0; index < 40; index += 1) {
    const letter = body[index];
    if (!/[a-f]/i.test(letter)) continue;
    // A nibble of eight or more means the letter is written in upper case.
    const expected = parseInt(hash[index], 16) >= 8 ? letter.toUpperCase() : letter.toLowerCase();
    if (letter !== expected) return false;
  }
  return true;
}

function normalizeAddress(value) {
  if (!isAddress(value)) {
    throw new Error("invalid address");
  }
  if (!hasValidChecksum(value)) {
    throw new Error("the checksum of the address is wrong");
  }
  return value.toLowerCase();
}

function formatUnits(value, decimals = 18) {
  const amount = BigInt(value);
  const text = amount.toString().padStart(decimals + 1, "0");
  const whole = text.slice(0, text.length - decimals);
  const fraction = text.slice(text.length - decimals).replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : whole;
}

function parseUnits(text, decimals = 18) {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(String(text));
  if (!match) {
    throw new Error("invalid decimal amount");
  }
  const fraction = match[2] || "";
  if (fraction.length > decimals) {
    throw new Error("too many decimal places");
  }
  return BigInt(match[1] + fraction.padEnd(decimals, "0"));
}

function encodeTransfer(to, amount) {
  const address = normalizeAddress(to).replace(/^0x/, "").padStart(64, "0");
  const value = BigInt(amount);
  if (value < 0n) {
    throw new Error("amount cannot be negative");
  }
  const encodedValue = value.toString(16).padStart(64, "0");
  return `0x${TRANSFER_SELECTOR}${address}${encodedValue}`;
}

module.exports = {
  isAddress,
  hasValidChecksum,
  normalizeAddress,
  formatUnits,
  parseUnits,
  encodeTransfer,
  TRANSFER_SELECTOR,
};
