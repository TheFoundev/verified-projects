"use strict";

const crypto = require("node:crypto");

const GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

function acceptKey(key) {
  return crypto.createHash("sha1").update(key + GUID).digest("base64");
}

// The biggest frame the server accepts.
const MAX_FRAME_BYTES = 1024 * 1024;

/**
 * A frame of the websocket protocol.
 *
 * A client has to mask its frames, a server must not, so the mask is a
 * parameter of the function.
 */
function encodeFrame(opcode, payload, mask = false) {
  const body = Buffer.isBuffer(payload) ? payload : Buffer.from(String(payload), "utf8");
  const masked = mask ? 0x80 : 0x00;

  let header;
  if (body.length < 126) {
    header = Buffer.alloc(2);
    header[0] = 0x80 | opcode;
    header[1] = masked | body.length;
  } else if (body.length < 65536) {
    header = Buffer.alloc(4);
    header[0] = 0x80 | opcode;
    header[1] = masked | 126;
    header.writeUInt16BE(body.length, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x80 | opcode;
    header[1] = masked | 127;
    header.writeBigUInt64BE(BigInt(body.length), 2);
  }

  if (!mask) {
    return Buffer.concat([header, body]);
  }

  // The mask is four bytes that change for every frame.
  const key = Buffer.alloc(4);
  for (let index = 0; index < 4; index += 1) {
    key[index] = Math.floor(Math.random() * 256);
  }
  const maskedBody = Buffer.from(body);
  for (let index = 0; index < maskedBody.length; index += 1) {
    maskedBody[index] ^= key[index % 4];
  }
  return Buffer.concat([header, key, maskedBody]);
}

/**
 * A text frame.
 *
 * A server sends its frames without a mask, a client has to mask its frames.
 */
function encodeText(text, mask = false) {
  return encodeFrame(0x1, text, mask);
}

/**
 * One frame of the websocket protocol.
 *
 * The size of a frame is limited, and the fragments of a message are put
 * together.
 */
function parseFrame(buffer, options = {}) {
  const maxBytes = options.maxBytes || MAX_FRAME_BYTES;
  const requireMask = options.requireMask === true;
  if (buffer.length < 2) {
    return null;
  }
  const opcode = buffer[0] & 0x0f;
  const masked = (buffer[1] & 0x80) !== 0;
  let length = buffer[1] & 0x7f;
  let offset = 2;
  if (length === 126) {
    if (buffer.length < 4) {
      return null;
    }
    length = buffer.readUInt16BE(2);
    offset = 4;
  } else if (length === 127) {
    if (buffer.length < 10) {
      return null;
    }
    const big = buffer.readBigUInt64BE(2);
    if (big > BigInt(maxBytes)) {
      throw new Error("frame too large");
    }
    length = Number(big);
    offset = 10;
  }
  if (length > maxBytes) {
    throw new Error("frame too large");
  }
  let mask = null;
  if (masked) {
    if (buffer.length < offset + 4) {
      return null;
    }
    mask = buffer.subarray(offset, offset + 4);
    offset += 4;
  } else if (requireMask) {
    // A client has to mask its frames.
    throw new Error("the client has to mask its frames");
  }
  if (buffer.length < offset + length) {
    return null;
  }
  const payload = Buffer.from(buffer.subarray(offset, offset + length));
  if (mask) {
    for (let index = 0; index < payload.length; index++) {
      payload[index] ^= mask[index % 4];
    }
  }
  return { opcode, payload, rest: buffer.subarray(offset + length) };
}

/**
 * The frames of a buffer, with the fragments of a message put together.
 */
function parseFrames(buffer, options = {}) {
  const frames = [];
  let rest = buffer;
  let fragment = null;
  let fragmentOpcode = 0;

  while (rest.length > 0) {
    let frame;
    try {
      frame = parseFrame(rest, options);
    } catch (error) {
      frames.push({ opcode: 0x8, payload: Buffer.from(String(error.message)), rest: Buffer.alloc(0) });
      break;
    }
    if (!frame) {
      break;
    }
    rest = frame.rest;

    if (frame.opcode === 0x0) {
      // A continuation of the message before it.
      fragment = fragment ? Buffer.concat([fragment, frame.payload]) : frame.payload;
    } else if (frame.opcode === 0x1 || frame.opcode === 0x2) {
      fragment = frame.payload;
      fragmentOpcode = frame.opcode;
    } else {
      frames.push(frame);
      continue;
    }

    if (frame.opcode === 0x0 || (frame.opcode !== 0x1 && frame.opcode !== 0x2)) {
      // The end of a fragmented message.
      frames.push({ opcode: fragmentOpcode, payload: fragment, rest: Buffer.alloc(0) });
      fragment = null;
    }
  }

  return { frames, rest };
}

function attachWebSocket(httpServer, handlers) {
  const clients = new Set();
  httpServer.on("upgrade", (request, socket) => {
    const key = request.headers["sec-websocket-key"];
    if (!key) {
      socket.destroy();
      return;
    }
    socket.write([
      "HTTP/1.1 101 Switching Protocols",
      "Upgrade: websocket",
      "Connection: Upgrade",
      "Sec-WebSocket-Accept: " + acceptKey(key),
      "",
      "",
    ].join("\r\n"));

    const client = {
      socket,
      send(text) {
        if (!socket.destroyed) {
          socket.write(encodeText(text));
        }
      },
      close() {
        socket.end();
      },
    };
    clients.add(client);
    let buffer = Buffer.alloc(0);

    socket.on("data", (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      let frame = parseFrame(buffer);
      while (frame !== null) {
        buffer = frame.rest;
        if (frame.opcode === 0x8) {
          socket.end();
          return;
        }
        if (frame.opcode === 0x9) {
          socket.write(encodeFrame(0x0a, frame.payload));
        } else if (frame.opcode === 0x1) {
          handlers.message(client, frame.payload.toString("utf8"), clients);
        }
        frame = parseFrame(buffer);
      }
    });
    socket.on("close", () => clients.delete(client));
    socket.on("error", () => clients.delete(client));
    handlers.open(client, clients);
  });
  return clients;
}

function broadcast(clients, data, except) {
  const text = typeof data === "string" ? data : JSON.stringify(data);
  for (const client of clients) {
    if (client !== except) {
      client.send(text);
    }
  }
}

module.exports = { acceptKey, encodeFrame, encodeText, parseFrame, parseFrames, attachWebSocket, broadcast, MAX_FRAME_BYTES };
