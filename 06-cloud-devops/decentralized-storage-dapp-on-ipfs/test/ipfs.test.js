"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const { IpfsClient } = require("../src/ipfs");
const { createIpfsServer } = require("../src/server");

/** A node that answers like an IPFS node. */
async function withNode(run) {
  const seen = [];
  const server = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      const body = Buffer.concat(chunks).toString("utf8");
      seen.push({ url: request.url, body });

      if (request.url.startsWith("/api/v0/add")) {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify({ Hash: "bafytest", Size: 5 }) + "\n");
        return;
      }
      if (request.url.startsWith("/api/v0/cat")) {
        response.writeHead(200);
        response.end("hello");
        return;
      }
      if (request.url.startsWith("/api/v0/pin/add")) {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify({ Pins: ["bafytest"] }));
        return;
      }
      if (request.url.startsWith("/api/v0/pin/ls")) {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify({ Keys: { "bafytest": "recursive" } }) + "\n");
        return;
      }
      response.writeHead(404);
      response.end("nope");
    });
  });
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    await run({ base, seen });
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
}

test("the client puts a file into the node", async () => {
  await withNode(async ({ base, seen }) => {
    const client = new IpfsClient({ apiUrl: base });
    const answer = await client.add("hello");

    assert.equal(answer.cid, "bafytest");
    assert.equal(answer.size, 5);
    assert.ok(seen[0].url.startsWith("/api/v0/add?pin=true"));
  });
});

test("the client reads a file back", async () => {
  await withNode(async ({ base }) => {
    const client = new IpfsClient({ apiUrl: base });
    const data = await client.cat("bafytest");
    assert.equal(data.toString("utf8"), "hello");
  });
});

test("the client pins a file", async () => {
  await withNode(async ({ base, seen }) => {
    const client = new IpfsClient({ apiUrl: base });
    assert.equal(await client.pin("bafytest"), true);
    assert.ok(seen[0].url.startsWith("/api/v0/pin/add"));
  });
});

test("the client reads the pins of the node", async () => {
  await withNode(async ({ base }) => {
    const client = new IpfsClient({ apiUrl: base });
    const pins = await client.pins();
    assert.equal(pins.bafytest, "recursive");
  });
});

test("an error of the node is reported", async () => {
  const client = new IpfsClient({ apiUrl: "http://127.0.0.1:1" });
  await assert.rejects(() => client.add("hello"), /IPFS node/);
});

test("the server puts the file into the node when there is one", async () => {
  await withNode(async ({ base }) => {
    const server = createIpfsServer(undefined, { node: new IpfsClient({ apiUrl: base }) });
    await new Promise((resolve) => server.listen(0, resolve));
    const port = server.address().port;
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/add`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ content: "hello" }),
      });
      const body = await response.json();
      assert.equal(body.cid, "bafytest");
      assert.equal(body.backend, "ipfs");
    } finally {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    }
  });
});
