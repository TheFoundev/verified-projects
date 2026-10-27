"use strict";

const TRANSFER_SELECTOR = "a9059cbb";

function isAddress(value) {
  return typeof value === "string" && /^0x[0-9a-fA-F]{40}$/.test(value);
}

function toWei(text) {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(String(text));
  if (!match) {
    throw new Error("invalid amount");
  }
  const fraction = match[2] || "";
  if (fraction.length > 18) {
    throw new Error("too many decimal places");
  }
  return BigInt(match[1] + fraction.padEnd(18, "0"));
}

function encodeTransfer(to, amount) {
  const address = to.toLowerCase().replace(/^0x/, "").padStart(64, "0");
  return `0x${TRANSFER_SELECTOR}${address}${amount.toString(16).padStart(64, "0")}`;
}

let account = null;

async function connect() {
  if (!window.ethereum) {
    document.getElementById("account").textContent = "no injected wallet found";
    return;
  }
  const accounts = await window.ethereum.request({ method: "eth_requestAccounts" });
  account = accounts[0];
  document.getElementById("account").textContent = account;
  const chainId = await window.ethereum.request({ method: "eth_chainId" });
  document.getElementById("chain").textContent = chainId;
  document.getElementById("transfer").disabled = false;
}

async function transfer() {
  const token = document.getElementById("token").value.trim();
  const to = document.getElementById("to").value.trim();
  const amount = toWei(document.getElementById("amount").value.trim());
  const receipt = document.getElementById("receipt");

  if (!isAddress(token) || !isAddress(to)) {
    receipt.textContent = "one of the addresses is not valid";
    return;
  }
  const data = encodeTransfer(to, amount);
  receipt.textContent = `data: ${data}`;
  if (!window.ethereum) {
    receipt.textContent += "\nno wallet available, only the call data was built";
    return;
  }
  try {
    const hash = await window.ethereum.request({
      method: "eth_sendTransaction",
      params: [{ from: account, to: token, data }],
    });
    receipt.textContent += `\ntx hash: ${hash}`;
  } catch (error) {
    receipt.textContent += `\n${error.message}`;
  }
}

document.getElementById("connect").addEventListener("click", connect);
document.getElementById("transfer").addEventListener("click", transfer);
