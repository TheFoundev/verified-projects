"use strict";

function headers() {
  return {
    "content-type": "application/json",
    "x-api-token": document.getElementById("token").value.trim(),
  };
}

async function refresh() {
  const response = await fetch("/api/secrets", { headers: headers() });
  const body = await response.json();
  const rows = document.querySelector("#secrets tbody");
  rows.textContent = "";
  for (const entry of response.ok ? body.secrets : []) {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = entry.name;
    const updated = document.createElement("td");
    updated.textContent = new Date(entry.updatedAt).toLocaleString();
    const actions = document.createElement("td");
    const view = document.createElement("button");
    view.textContent = "view";
    view.addEventListener("click", () => reveal(entry.name));
    const remove = document.createElement("button");
    remove.textContent = "delete";
    remove.addEventListener("click", () => removeSecret(entry.name));
    actions.append(view, remove);
    row.append(name, updated, actions);
    rows.appendChild(row);
  }

  const audit = await fetch("/api/audit", { headers: headers() });
  const auditBody = await audit.json();
  const list = document.getElementById("audit");
  list.textContent = "";
  for (const entry of audit.ok ? auditBody.audit : []) {
    const item = document.createElement("li");
    item.textContent = `${new Date(entry.at).toLocaleString()} ${entry.action} ${entry.name}`;
    list.appendChild(item);
  }
}

async function save() {
  const name = document.getElementById("name").value;
  const value = document.getElementById("value").value;
  const response = await fetch("/api/secrets", { method: "POST", headers: headers(), body: JSON.stringify({ name, value }) });
  if (!response.ok) {
    alert((await response.json()).error);
    return;
  }
  document.getElementById("value").value = "";
  await refresh();
}

async function reveal(name) {
  const response = await fetch(`/api/secrets/${encodeURIComponent(name)}`, { headers: headers() });
  const body = await response.json();
  alert(response.ok ? `${name} = ${body.value}` : body.error);
}

async function removeSecret(name) {
  await fetch(`/api/secrets/${encodeURIComponent(name)}`, { method: "DELETE", headers: headers() });
  await refresh();
}

document.getElementById("save").addEventListener("click", save);
document.getElementById("token").addEventListener("change", refresh);
refresh();
