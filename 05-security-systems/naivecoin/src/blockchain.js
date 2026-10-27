"use strict";

const crypto = require("node:crypto");

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function normalizeTransaction(transaction) {
  return {
    from: String(transaction.from),
    to: String(transaction.to),
    amount: Number(transaction.amount),
    timestamp: Number(transaction.timestamp || 0),
  };
}

class Transaction {
  constructor(from, to, amount, timestamp = Date.now()) {
    this.from = String(from);
    this.to = String(to);
    this.amount = Number(amount);
    this.timestamp = Number(timestamp);
  }

  toJSON() {
    return normalizeTransaction(this);
  }
}

class Block {
  constructor(index, timestamp, transactions, previousHash, nonce = 0) {
    this.index = index;
    this.timestamp = timestamp;
    this.transactions = transactions.map(normalizeTransaction);
    this.previousHash = previousHash;
    this.nonce = nonce;
  }

  hash() {
    return sha256(JSON.stringify(this.toJSON()));
  }

  toJSON() {
    return {
      index: this.index,
      timestamp: this.timestamp,
      transactions: this.transactions,
      previousHash: this.previousHash,
      nonce: this.nonce,
    };
  }

  static fromJSON(value) {
    const block = new Block(value.index, value.timestamp, value.transactions, value.previousHash, value.nonce);
    return block;
  }
}

class Blockchain {
  constructor(difficulty = 3) {
    this.difficulty = difficulty;
    this.chain = [new Block(0, 0, [], "0".repeat(64))];
    this.pending = [];
  }

  get lastBlock() {
    return this.chain[this.chain.length - 1];
  }

  addTransaction(from, to, amount) {
    const value = Number(amount);
    if (!from || !to || !Number.isFinite(value) || value <= 0) {
      throw new Error("transaction needs a sender, a recipient and a positive amount");
    }
    const transaction = new Transaction(from, to, value);
    this.pending.push(transaction);
    return transaction;
  }

  minePending(miner = "miner") {
    const reward = new Transaction("network", miner, 1, Date.now());
    const block = new Block(
      this.chain.length,
      Date.now(),
      [...this.pending, reward],
      this.lastBlock.hash(),
    );
    const prefix = "0".repeat(this.difficulty);
    while (!block.hash().startsWith(prefix)) {
      block.nonce += 1;
    }
    this.chain.push(block);
    this.pending = [];
    return block;
  }

  isValid() {
    if (!Array.isArray(this.chain) || this.chain.length === 0) {
      return false;
    }
    const prefix = "0".repeat(this.difficulty);
    for (let index = 1; index < this.chain.length; index++) {
      const current = this.chain[index];
      const previous = this.chain[index - 1];
      if (current.index !== index) {
        return false;
      }
      if (current.previousHash !== previous.hash()) {
        return false;
      }
      if (!current.hash().startsWith(prefix)) {
        return false;
      }
    }
    return true;
  }

  balance(address) {
    let total = 0;
    for (const block of this.chain) {
      for (const transaction of block.transactions) {
        if (transaction.to === address) {
          total += transaction.amount;
        }
        if (transaction.from === address) {
          total -= transaction.amount;
        }
      }
    }
    return total;
  }

  toJSON() {
    return { difficulty: this.difficulty, chain: this.chain, pending: this.pending };
  }

  static fromJSON(value) {
    const blockchain = new Blockchain(value.difficulty || 3);
    blockchain.chain = value.chain.map(Block.fromJSON);
    blockchain.pending = (value.pending || []).map((item) => new Transaction(item.from, item.to, item.amount, item.timestamp));
    return blockchain;
  }
}

module.exports = { Transaction, Block, Blockchain, sha256 };
