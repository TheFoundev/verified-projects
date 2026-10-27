"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { deriveKey, encrypt, decrypt, newSalt } = require("./crypto");

// The salt of a store is random and is kept with the data. A fixed salt would
// give the same key for the same password on every machine.
const DEFAULT_SALT = newSalt();

class SecretStore {
  constructor(file, password) {
    this.file = file;
    this.data = { version: 1, salt: DEFAULT_SALT, secrets: {}, audit: [] };
    this.key = null;
    if (password === undefined || password === "") {
      throw new Error("a master password is required");
    }
    this.key = deriveKey(password, this.data.salt);
    if (fs.existsSync(file)) {
      const loaded = JSON.parse(fs.readFileSync(file, "utf8"));
      this.data.salt = loaded.salt || DEFAULT_SALT;
      this.key = deriveKey(password, this.data.salt);
      this.data.secrets = loaded.secrets || {};
      this.data.audit = loaded.audit || [];
      this.data.version = loaded.version || 1;
    }
  }

  record(action, name) {
    this.data.audit.push({ action, name, at: Date.now() });
    if (this.data.audit.length > 200) {
      this.data.audit = this.data.audit.slice(-200);
    }
  }

  set(name, value) {
    if (!name) {
      throw new Error("a secret name is required");
    }
    if (value === undefined || value === null || value === "") {
      throw new Error("a secret value is required");
    }
    this.data.secrets[name] = {
      ...encrypt(this.key, value),
      updatedAt: Date.now(),
    };
    this.record("write", name);
    this.save();
    return name;
  }

  get(name) {
    const entry = this.data.secrets[name];
    if (!entry) {
      return null;
    }
    return decrypt(this.key, entry);
  }

  remove(name) {
    if (!this.data.secrets[name]) {
      return false;
    }
    delete this.data.secrets[name];
    this.record("delete", name);
    this.save();
    return true;
  }

  list() {
    return Object.keys(this.data.secrets)
      .sort()
      .map((name) => ({ name, updatedAt: this.data.secrets[name].updatedAt }));
  }

  audit() {
    return [...this.data.audit];
  }

  save() {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    fs.writeFileSync(this.file, JSON.stringify(this.data, null, 2));
  }
}

module.exports = { SecretStore };
