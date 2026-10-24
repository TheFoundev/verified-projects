"use strict";

/**
 * A client for the HTTP API of IPFS.
 *
 * The API of a node is reached over a URL, so the client works against a
 * node on the machine and against one behind a gateway.
 */

const DEFAULT_API = "http://127.0.0.1:5001";

/**
 * The client of an IPFS node.
 */
class IpfsClient {
  /**
   * @param {object} options
   * @param {string} options.apiUrl  the address of the API of the node
   * @param {typeof fetch} options.fetchImpl  the fetch to use
   */
  constructor({ apiUrl = DEFAULT_API, fetchImpl = fetch } = {}) {
    this.apiUrl = String(apiUrl).replace(/\/$/, "");
    this.fetchImpl = fetchImpl;
  }

  /** The answer of the node, with the body already read. */
  async call(path, options = {}) {
    let response;
    try {
      response = await this.fetchImpl(`${this.apiUrl}${path}`, {
        method: options.method || "POST",
        headers: options.headers || {},
        body: options.body,
      });
    } catch (error) {
      throw new Error(`the IPFS node at ${this.apiUrl} could not be reached: ${error && error.message}`);
    }
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(`the IPFS node answered with ${response.status}: ${text.slice(0, 120)}`);
    }
    return response;
  }

  /**
   * Put a file into the node and return its cid.
   *
   * The node is asked to pin the file, so it stays in the node.
   */
  async add(content, { pin = true, name = "file" } = {}) {
    const data = Buffer.isBuffer(content) ? content : Buffer.from(String(content), "utf8");
    const boundary = "----foundev" + Date.now().toString(16);
    const head = Buffer.from(
      `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="file"; filename="${name}"\r\n` +
        "Content-Type: application/octet-stream\r\n\r\n",
      "utf8"
    );
    const tail = Buffer.from(`\r\n--${boundary}--\r\n`, "utf8");
    const body = Buffer.concat([head, data, tail]);

    const response = await this.call(`/api/v0/add?pin=${pin ? "true" : "false"}`, {
      headers: { "content-type": `multipart/form-data; boundary=${boundary}` },
      body,
    });
    const text = await response.text();
    const answer = JSON.parse(text.split("\n")[0] || "{}");
    if (!answer.Hash) {
      throw new Error("the IPFS node did not answer with a hash");
    }
    return { cid: answer.Hash, size: Number(answer.Size || data.length) };
  }

  /** Read a file back from the node. */
  async cat(cid) {
    const response = await this.call(`/api/v0/cat?arg=${encodeURIComponent(cid)}`);
    return Buffer.from(await response.arrayBuffer());
  }

  /** Pin a file, so the node keeps it. */
  async pin(cid) {
    await this.call(`/api/v0/pin/add?arg=${encodeURIComponent(cid)}`);
    return true;
  }

  /** The pins of the node. */
  async pins() {
    const response = await this.call("/api/v0/pin/ls?type=recursive");
    const text = await response.text();
    const keys = {};

    // The answer of the node is either an object with a Keys field or one
    // object per line, depending on the version.
    for (const line of text.split("\n")) {
      if (!line.trim()) continue;
      try {
        const entry = JSON.parse(line);
        if (entry.Keys && typeof entry.Keys === "object") {
          for (const [key, value] of Object.entries(entry.Keys)) {
            keys[key] = typeof value === "object" ? value.Type : value;
          }
          continue;
        }
        if (entry.Key) keys[entry.Key] = entry.Type;
      } catch {
        /* the node writes something that is not json, it is skipped */
      }
    }
    return keys;
  }

  /** The version of the node. */
  async version() {
    const response = await this.call("/api/v0/version", { method: "GET" });
    const text = await response.text();
    try {
      return JSON.parse(text);
    } catch {
      return { Version: text.trim() };
    }
  }
}

module.exports = { IpfsClient, DEFAULT_API };
