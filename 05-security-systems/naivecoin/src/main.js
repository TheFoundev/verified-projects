"use strict";

const { Blockchain } = require("./blockchain");

const chain = new Blockchain(3);
chain.addTransaction("alice", "bob", 10);
chain.addTransaction("bob", "carol", 4);
console.log("mining block 1...");
chain.minePending("alice");
chain.addTransaction("carol", "alice", 2);
console.log("mining block 2...");
chain.minePending("alice");

for (const block of chain.chain) {
  console.log(`block ${block.index} hash=${block.hash().slice(0, 16)} nonce=${block.nonce}`);
}
console.log("alice balance:", chain.balance("alice"));
console.log("bob balance:", chain.balance("bob"));
console.log("valid chain:", chain.isValid());
