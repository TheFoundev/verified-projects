"use strict";

const http = require("node:http");
const { servePublic } = require("./static");

function createDappServer() {
  return http.createServer((request, response) => {
    const url = new URL(request.url, "http://localhost");
    if (url.pathname === "/api/info") {
      response.writeHead(200, { "content-type": "application/json; charset=utf-8" });
      response.end(JSON.stringify({ name: "web3 dapp demo", chainId: "0x1", transferSelector: "0xa9059cbb" }));
      return;
    }
    if (url.pathname === "/" || url.pathname === "/index.html" || url.pathname === "/app.js") {
      servePublic(request, response);
      return;
    }
    response.writeHead(404, { "content-type": "text/plain" });
    response.end("not found");
  });
}

if (require.main === module) {
  const port = Number(process.env.PORT || 5173);
  createDappServer().listen(port, () => {
    console.log(`dapp on http://localhost:${port}`);
  });
}

module.exports = { createDappServer };
