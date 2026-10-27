"use strict";

/**
 * Keccak-256, the hash Ethereum uses.
 *
 * It is the same permutation as SHA3-256 but with the padding of the
 * original Keccak, so the two give different digests for the same input.
 *
 * The lanes are 64 bit numbers, so they are held in BigInt. It is not fast,
 * but the addresses of a wallet are checked once and not in a loop.
 */

const ROUNDS = 24;

const ROUND_CONSTANTS = [
  0x0000000000000001n, 0x0000000000008082n, 0x800000000000808an, 0x8000000080008000n,
  0x000000000000808bn, 0x0000000080000001n, 0x8000000080008081n, 0x8000000000008009n,
  0x000000000000008an, 0x0000000000000088n, 0x0000000080008009n, 0x000000008000000an,
  0x000000008000808bn, 0x800000000000008bn, 0x8000000000008089n, 0x8000000000008003n,
  0x8000000000008002n, 0x8000000000000080n, 0x000000000000800an, 0x800000008000000an,
  0x8000000080008081n, 0x8000000000008080n, 0x0000000080000001n, 0x8000000080008008n,
];

const ROTATIONS = [
  0, 1, 62, 28, 27,
  36, 44, 6, 55, 20,
  3, 10, 43, 25, 39,
  41, 45, 15, 21, 8,
  18, 2, 61, 56, 14,
];

const MASK = (1n << 64n) - 1n;

function rotateLeft(value, shift) {
  if (shift === 0) return value;
  return ((value << BigInt(shift)) | (value >> BigInt(64 - shift))) & MASK;
}

function keccakF(state) {
  for (let round = 0; round < ROUNDS; round += 1) {
    // Theta
    const c = new Array(5);
    for (let x = 0; x < 5; x += 1) {
      c[x] = state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20];
    }
    for (let x = 0; x < 5; x += 1) {
      const d = c[(x + 4) % 5] ^ rotateLeft(c[(x + 1) % 5], 1);
      for (let y = 0; y < 25; y += 5) {
        state[x + y] ^= d;
      }
    }

    // Rho and pi
    const b = new Array(25);
    for (let x = 0; x < 5; x += 1) {
      for (let y = 0; y < 5; y += 1) {
        b[y + 5 * ((2 * x + 3 * y) % 5)] = rotateLeft(state[x + 5 * y], ROTATIONS[x + 5 * y]);
      }
    }

    // Chi
    for (let y = 0; y < 25; y += 5) {
      for (let x = 0; x < 5; x += 1) {
        state[x + y] = b[x + y] ^ ((~b[((x + 1) % 5) + y] & MASK) & b[((x + 2) % 5) + y]);
      }
    }

    // Iota
    state[0] ^= ROUND_CONSTANTS[round];
  }
  return state;
}

/**
 * The Keccak-256 digest of a buffer, as lowercase hex.
 */
function keccak256(input) {
  const bytes = Buffer.from(input);
  const rate = 136; // 1088 bits, the rate of Keccak-256

  const padded = Buffer.alloc(Math.ceil((bytes.length + 1) / rate) * rate);
  bytes.copy(padded);
  padded[bytes.length] = 0x01;
  padded[padded.length - 1] |= 0x80;

  const state = new Array(25).fill(0n);
  for (let offset = 0; offset < padded.length; offset += rate) {
    for (let index = 0; index < rate / 8; index += 1) {
      let lane = 0n;
      for (let byte = 7; byte >= 0; byte -= 1) {
        lane = (lane << 8n) | BigInt(padded[offset + index * 8 + byte]);
      }
      state[index] ^= lane;
    }
    keccakF(state);
  }

  const digest = Buffer.alloc(32);
  for (let index = 0; index < 4; index += 1) {
    let lane = state[index];
    for (let byte = 0; byte < 8; byte += 1) {
      digest[index * 8 + byte] = Number(lane & 0xffn);
      lane >>= 8n;
    }
  }
  return digest.toString("hex");
}

module.exports = { keccak256 };
