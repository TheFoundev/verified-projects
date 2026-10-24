"use strict";

const path = require("node:path");
const http = require("node:http");
const { createStore } = require("./store");
const { servePublic } = require("./static");
const { IpfsClient } = require("./ipfs");

function readBody(request) {
  return new Promise((resolve, reject) => {
    let data = "";
    request.on("data", (chunk) => {
      data += chunk;
    });
    request.on("end", () => resolve(data));
    request.on("error", reject);
  });
}

function createIpfsServer(store = createStore(path.join(__dirname, "..", "data")), options = {}) {
  // When the address of an IPFS node is given, the files go into the node
  // and are pinned there. Without it the local store is used.
  const node = options.node || (process.env.IPFS_API_URL ? new IpfsClient({ apiUrl: process.env.IPFS_API_URL }) : null);
  return http.createServer(async (request, response) => {
    const url = new URL(request.url, "http://localhost");
    const send = (status, body) => {
      response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
      response.end(JSON.stringify(body));
    };

    try {
      if (request.method === "POST" && url.pathname === "/api/add") {
        const raw = await readBody(request);
        let content = raw;
        if ((request.headers["content-type"] || "").includes("application/json")) {
          content = JSON.parse(raw || "{}").content;
          if (content === undefined) {
            send(400, { error: "content is required" });
            return;
          }
        }
        if (node) {
          try {
            const answer = await node.add(content);
            send(201, { ...answer, backend: "ipfs" });
          } catch (error) {
            send(502, { error: `the IPFS node could not take the file: ${error.message}` });
          }
          return;
        }
        const cid = store.add(content);
        send(201, { cid, size: Buffer.byteLength(String(content)), backend: "local" });
        return;
      }

      if (request.method === "POST" && url.pathname === "/api/pin") {
        if (!node) {
          send(400, { error: "there is no IPFS node to pin the file in" });
          return;
        }
        const body = await readBody(request);
        let cid = "";
        try {
          const parsed = JSON.parse(body || "{}");
          cid = String(parsed.cid || "");
        } catch {
          cid = "";
        }
        if (!cid) {
          send(400, { error: "a cid is required" });
          return;
        }
        try {
          await node.pin(cid);
          send(200, { cid, pinned: true });
        } catch (error) {
          send(502, { error: error.message });
        }
        return;
      }

      if (request.method === "GET" && url.pathname === "/api/pins") {
        if (!node) {
          send(200, { pins: {} });
          return;
        }
        try {
          send(200, { pins: await node.pins() });
        } catch (error) {
          send(502, { error: error.message });
        }
        return;
      }

      if (request.method === "GET" && url.pathname.startsWith("/api/cid/")) {
        const cid = decodeURIComponent(url.pathname.slice("/api/cid/".length));
        if (node) {
          try {
            const data = await node.cat(cid);
            response.writeHead(200, { "content-type": "text/plain; charset=utf-8" });
            response.end(data.toString("utf8"));
          } catch (error) {
            send(404, { error: error.message });
          }
          return;
        }
        try {
          const data = store.get(cid);
          if (data === null) {
            send(404, { error: "not found" });
            return;
          }
          response.writeHead(200, { "content-type": "text/plain; charset=utf-8" });
          response.end(data.toString("utf8"));
        } catch (error) {
          send(400, { error: error.message });
        }
        return;
      }

      if (url.pathname === "/" || url.pathname === "/index.html" || url.pathname === "/app.js") {
        servePublic(request, response);
        return;
      }

      send(404, { error: "not found" });
    } catch (error) {
      send(400, { error: error.message });
    }
  });
}

if (require.main === module) {
  const port = Number(process.env.PORT || 4001);
  const server = createIpfsServer();
  server.listen(port, () => {
    console.log(`content store on http://localhost:${port}`);
  });
}

module.exports = { createIpfsServer, IpfsClient };
