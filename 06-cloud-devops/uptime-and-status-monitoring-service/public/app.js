"use strict";

async function refresh() {
  const response = await fetch("/api/monitors");
  const body = await response.json();
  const rows = document.querySelector("#monitors tbody");
  rows.textContent = "";

  const monitors = body.monitors || [];
  const overall = document.getElementById("overall");
  const down = monitors.filter((entry) => entry.status === "down").length;
  overall.textContent = monitors.length === 0
    ? "no monitors configured"
    : down > 0 ? `${down} of ${monitors.length} monitors down` : `all ${monitors.length} monitors up`;
  overall.className = down > 0 ? "down" : "up";

  for (const monitor of monitors) {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = monitor.name;
    const url = document.createElement("td");
    url.textContent = monitor.url;
    const status = document.createElement("td");
    status.textContent = monitor.status;
    status.className = monitor.status;
    const latency = document.createElement("td");
    latency.textContent = monitor.latencyMs === null ? "-" : `${monitor.latencyMs} ms`;
    const actions = document.createElement("td");
    const check = document.createElement("button");
    check.textContent = "check now";
    check.addEventListener("click", () => checkNow(monitor.id));
    const remove = document.createElement("button");
    remove.textContent = "remove";
    remove.addEventListener("click", () => removeMonitor(monitor.id));
    actions.append(check, remove);
    row.append(name, url, status, latency, actions);
    rows.appendChild(row);
  }
}

async function addMonitor(event) {
  event.preventDefault();
  await fetch("/api/monitors", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      name: document.getElementById("name").value,
      url: document.getElementById("url").value,
      intervalSeconds: Number(document.getElementById("interval").value) || 60,
    }),
  });
  event.target.reset();
  await refresh();
}

async function checkNow(id) {
  await fetch(`/api/monitors/${id}/check`, { method: "POST" });
  await refresh();
}

async function removeMonitor(id) {
  await fetch(`/api/monitors/${id}`, { method: "DELETE" });
  await refresh();
}

document.getElementById("add").addEventListener("submit", addMonitor);
refresh();
setInterval(refresh, 5000);
