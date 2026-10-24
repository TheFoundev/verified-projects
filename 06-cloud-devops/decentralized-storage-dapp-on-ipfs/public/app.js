"use strict";

async function addContent() {
  const content = document.getElementById("content").value;
  const response = await fetch("/api/add", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ content }),
  });
  const body = await response.json();
  document.getElementById("cid").textContent = response.ok ? body.cid : body.error;
}

async function fetchContent() {
  const cid = document.getElementById("target").value.trim();
  const output = document.getElementById("output");
  if (!cid) {
    output.textContent = "enter a cid first";
    return;
  }
  const response = await fetch(`/api/cid/${encodeURIComponent(cid)}`);
  output.textContent = response.ok
    ? await response.text()
    : (await response.json()).error;
}

document.getElementById("add").addEventListener("click", addContent);
document.getElementById("fetch").addEventListener("click", fetchContent);
