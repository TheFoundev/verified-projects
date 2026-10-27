"use strict";

const path = require("node:path");
const http = require("node:http");
const crypto = require("node:crypto");
const { equal } = require("./crypto");
const { SecretStore } = require("./store");
const { servePublic } = require("./static");

const TOKEN = process.env.SECRETS_API_TOKEN || crypto.randomBytes(16).toString("hex");

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

function createSecretsServer(store, token = TOKEN) {
  return http.createServer(async (request, response) => {
    const url = new URL(request.url, "http://localhost");
    const send = (status, body) => {
      response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
      response.end(JSON.stringify(body));
    };

    // The token is compared without a timing difference, so the answer does
    // not say how much of the token was right.
    if (url.pathname.startsWith("/api/") && !equal(request.headers["x-api-token"] || "", token)) {
      send(401, { error: "valid x-api-token header required" });
      return;
    }

    try {
      if (request.method === "GET" && url.pathname === "/api/secrets") {
        send(200, { secrets: store.list() });
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/secrets") {
        const body = JSON.parse((await readBody(request)) || "{}");
        const name = store.set(body.name, body.value);
        send(201, { name });
        return;
      }
      if (request.method === "GET" && url.pathname.startsWith("/api/secrets/")) {
        const name = decodeURIComponent(url.pathname.slice("/api/secrets/".length));
        const value = store.get(name);
        if (value === null) {
          send(404, { error: "not found" });
          return;
        }
        send(200, { name, value });
        return;
      }
      if (request.method === "DELETE" && url.pathname.startsWith("/api/secrets/")) {
        const name = decodeURIComponent(url.pathname.slice("/api/secrets/".length));
        if (!store.remove(name)) {
          send(404, { error: "not found" });
          return;
        }
        send(200, { name, deleted: true });
        return;
      }
      if (request.method === "GET" && url.pathname === "/api/audit") {
        send(200, { audit: store.audit() });
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
  const file = process.env.SECRETS_FILE || path.join(__dirname, "..", "data", "secrets.json");
  const password = process.env.SECRETS_PASSWORD || "change-me";
  const store = new SecretStore(file, password);
  const port = Number(process.env.PORT || 5000);
  createSecretsServer(store).listen(port, () => {
    console.log(`secrets ui on http://localhost:${port}`);
    console.log(`api token: ${TOKEN}`);
  });
}

module.exports = { createSecretsServer, TOKEN };
