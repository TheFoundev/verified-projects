"use strict";

const crypto = require("node:crypto");

const ALGORITHM = "aes-256-gcm";
const ITERATIONS = 120000;

function deriveKey(password, salt) {
  const material = crypto.pbkdf2Sync(
    String(password),
    Buffer.from(salt, "base64"),
    ITERATIONS,
    32,
    "sha256",
  );
  return material;
}

function encrypt(key, plaintext) {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv(ALGORITHM, key, iv);
  const ciphertext = Buffer.concat([cipher.update(String(plaintext), "utf8"), cipher.final()]);
  return {
    iv: iv.toString("base64"),
    ciphertext: ciphertext.toString("base64"),
    tag: cipher.getAuthTag().toString("base64"),
  };
}

function decrypt(key, envelope) {
  const decipher = crypto.createDecipheriv(ALGORITHM, key, Buffer.from(envelope.iv, "base64"));
  decipher.setAuthTag(Buffer.from(envelope.tag, "base64"));
  return Buffer.concat([
    decipher.update(Buffer.from(envelope.ciphertext, "base64")),
    decipher.final(),
  ]).toString("utf8");
}

/**
 * A new random salt.
 *
 * The salt is stored with the data, so the same master password gives a
 * different key on every machine and a dictionary of passwords does not help
 * against a stolen store.
 */
function newSalt() {
  return crypto.randomBytes(16).toString("base64");
}

/**
 * The same value, compared without a timing difference.
 */
function equal(first, second) {
  const left = Buffer.from(String(first));
  const right = Buffer.from(String(second));
  if (left.length !== right.length) return false;
  return crypto.timingSafeEqual(left, right);
}

module.exports = { deriveKey, encrypt, decrypt, ITERATIONS, newSalt, equal };
