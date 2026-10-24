"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { cidFromBytes } = require("./cid");

function createStore(directory) {
  fs.mkdirSync(directory, { recursive: true });

  const add = (content) => {
    const data = Buffer.isBuffer(content) ? content : Buffer.from(String(content), "utf8");
    const cid = cidFromBytes(data);
    fs.writeFileSync(path.join(directory, cid), data);
    return cid;
  };

  const get = (cid) => {
    if (!/^[a-z0-9]+$/.test(String(cid))) {
      throw new Error("invalid cid");
    }
    const file = path.join(directory, cid);
    if (!fs.existsSync(file)) {
      return null;
    }
    const data = fs.readFileSync(file);
    if (cidFromBytes(data) !== cid) {
      throw new Error("stored content does not match its cid");
    }
    return data;
  };

  const has = (cid) => get(cid) !== null;
  const list = () => fs.readdirSync(directory).sort();

  return { directory, add, get, has, list };
}

module.exports = { createStore };
