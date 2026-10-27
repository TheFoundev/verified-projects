"use strict";

const path = require("node:path");
const http = require("node:http");
const { openDatabase } = require("./db");
const { checkOnce } = require("./monitor");
const { servePublic } = require("./static");

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

function createStatusServer(store) {
  return http.createServer(async (request, response) => {
    const url = new URL(request.url, "http://localhost");
    const send = (status, body) => {
      response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
      response.end(JSON.stringify(body));
    };

    try {
      if (request.method === "GET" && url.pathname === "/api/health") {
        send(200, { status: "ok" });
        return;
      }
      if (request.method === "GET" && url.pathname === "/api/monitors") {
        send(200, { monitors: store.summary() });
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/monitors") {
        const body = JSON.parse((await readBody(request)) || "{}");
        const monitor = store.createMonitor(body);
        send(201, { monitor });
        return;
      }
      if (request.method === "POST" && url.pathname.startsWith("/api/monitors/") && url.pathname.endsWith("/check")) {
        const id = Number(url.pathname.slice("/api/monitors/".length).split("/")[0]);
        const monitor = store.getMonitor(id);
        if (!monitor) {
          send(404, { error: "monitor not found" });
          return;
        }
        const result = await checkOnce(monitor);
        store.recordCheck({ monitorId: monitor.id, ...result });
        send(200, { monitor, result });
        return;
      }
      if (request.method === "GET" && url.pathname.startsWith("/api/monitors/")) {
        const [idPart, tail] = url.pathname.slice("/api/monitors/".length).split("/");
        const monitor = store.getMonitor(Number(idPart));
        if (!monitor) {
          send(404, { error: "monitor not found" });
          return;
        }
        if (tail === "checks") {
          send(200, { checks: store.recentChecks(monitor.id) });
          return;
        }
        if (tail === "check") {
          const result = await checkOnce(monitor);
          store.recordCheck({ monitorId: monitor.id, ...result });
          send(200, { monitor, result });
          return;
        }
        send(200, { monitor });
        return;
      }
      if (request.method === "DELETE" && url.pathname.startsWith("/api/monitors/")) {
        const id = Number(url.pathname.slice("/api/monitors/".length));
        if (!store.deleteMonitor(id)) {
          send(404, { error: "monitor not found" });
          return;
        }
        send(200, { id, deleted: true });
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
  const file = process.env.STATUS_DB || path.join(__dirname, "..", "data", "status.db");
  const store = openDatabase(file);
  const port = Number(process.env.PORT || 3000);
  const server = createStatusServer(store);
  server.listen(port, () => {
    console.log(`status dashboard on http://localhost:${port}`);
  });
}

module.exports = { createStatusServer };
