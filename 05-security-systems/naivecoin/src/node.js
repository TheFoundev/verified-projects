"use strict";

const http = require("node:http");
const { attachWebSocket, broadcast } = require("./ws");
const { Blockchain } = require("./blockchain");

function readBody(request) {
  return new Promise((resolve, reject) => {
    let data = "";
    request.on("data", (chunk) => {
      data += chunk;
    });
    request.on("end", () => {
      if (!data) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(data));
      } catch (error) {
        reject(new Error("request body is not valid JSON"));
      }
    });
    request.on("error", reject);
  });
}

function createNodeServer(blockchain = new Blockchain(3)) {
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, "http://localhost");
    const send = (status, body) => {
      response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
      response.end(JSON.stringify(body));
    };

    try {
      if (request.method === "GET" && url.pathname === "/") {
        send(200, {
          name: "naivecoin",
          blocks: blockchain.chain.length,
          pending: blockchain.pending.length,
          valid: blockchain.isValid(),
        });
        return;
      }
      if (request.method === "GET" && url.pathname === "/api/blocks") {
        send(200, blockchain.chain);
        return;
      }
      if (request.method === "GET" && url.pathname === "/api/balance") {
        const address = url.searchParams.get("address") || "";
        send(200, { address, balance: blockchain.balance(address) });
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/transactions") {
        const body = await readBody(request);
        const transaction = blockchain.addTransaction(body.from, body.to, body.amount);
        broadcast(server.clients || new Set(), { type: "transaction", transaction });
        send(201, transaction);
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/mine") {
        const body = await readBody(request).catch(() => ({}));
        const block = blockchain.minePending(body.miner || "miner");
        broadcast(server.clients || new Set(), { type: "block", block });
        send(200, block);
        return;
      }
      send(404, { error: "not found" });
    } catch (error) {
      send(400, { error: error.message });
    }
  });

  const clients = attachWebSocket(server, {
    open(client) {
      client.send(JSON.stringify({ type: "chain", chain: blockchain.chain }));
    },
    message(client, text, peers) {
      let message;
      try {
        message = JSON.parse(text);
      } catch (error) {
        client.send(JSON.stringify({ type: "error", error: "invalid json" }));
        return;
      }
      if (message.type === "transaction") {
        try {
          const transaction = blockchain.addTransaction(message.from, message.to, message.amount);
          broadcast(peers, { type: "transaction", transaction });
        } catch (error) {
          client.send(JSON.stringify({ type: "error", error: error.message }));
        }
      } else if (message.type === "request-chain") {
        client.send(JSON.stringify({ type: "chain", chain: blockchain.chain }));
      }
    },
  });
  server.clients = clients;
  return server;
}

module.exports = { createNodeServer, readBody };
