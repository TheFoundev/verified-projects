/* The runtime of the project console.
   It fetches from the console API, renders the panels and runs the project's
   own demo command. Nothing on the page is a mock-up. */

const API = "/api";
const state = { data: {} };

async function api(path, options = {}) {
  const response = await fetch(API + path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch (error) { payload = { raw: text }; }
  if (!response.ok) {
    const message = (payload && (payload.error || payload.message)) || response.statusText;
    throw new Error(message);
  }
  return payload;
}

function toast(message, kind = "ok") {
  let host = document.querySelector(".toasts");
  if (!host) {
    host = document.createElement("div");
    host.className = "toasts";
    document.body.appendChild(host);
  }
  const node = document.createElement("div");
  node.className = "toast " + (kind === "error" ? "error" : "");
  node.textContent = message;
  host.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function paintStats(payload) {
  const values = (payload && payload.stats) || payload || {};
  document.querySelectorAll("[data-stat]").forEach((node) => {
    const value = values[node.dataset.stat];
    if (value !== undefined && value !== null) node.textContent = value;
  });
}

function paintChart(payload) {
  const series = (payload && payload.series) || [];
  document.querySelectorAll("svg.chart").forEach((svg) => {
    if (!series.length) return;
    const width = 640, height = 168, gap = 6;
    const max = Math.max(...series.map((point) => Number(point.value) || 0), 1);
    const bar = (width - gap * (series.length - 1)) / series.length;
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Lines of code by language");
    svg.innerHTML = series.map((point, index) => {
      const value = Number(point.value) || 0;
      const h = Math.max(3, (value / max) * (height - 34));
      const x = index * (bar + gap);
      return '<rect class="bar" x="' + x + '" y="' + (height - h - 22) + '" width="' + bar +
             '" height="' + h + '" rx="3"><title>' + escapeHtml(point.label || "") + ": " + value + " lines</title></rect>" +
             '<text x="' + (x + bar / 2) + '" y="' + (height - 6) + '" text-anchor="middle" font-size="10" fill="currentColor" opacity=".7">' + escapeHtml(point.label || "") + "</text>";
    }).join("") +
      '<line class="axis" x1="0" y1="' + (height - 21) + '" x2="' + width + '" y2="' + (height - 21) + '"></line>';
  });
}

function renderLists(payload) {
  const lists = (payload && payload.lists) || payload || {};
  document.querySelectorAll("[data-list]").forEach((node) => {
    const rows = lists[node.dataset.list] || [];
    const columns = Number(node.dataset.columns || 0);
    if (!rows.length) {
      node.innerHTML = '<tr><td colspan="' + (columns || 4) + '" class="muted">Nothing to show.</td></tr>';
      return;
    }
    node.innerHTML = rows.map((row) => {
      const cells = Object.values(row).map((cell) => "<td>" + escapeHtml(cell) + "</td>").join("");
      return "<tr>" + cells + "</tr>";
    }).join("");
  });
}

function renderDemo(demo) {
  const label = document.querySelector("[data-demo-label]");
  const command = document.querySelector("[data-demo-command]");
  if (!demo) return;
  if (label) label.textContent = demo.label || "Run the demo";
  if (command) command.textContent = "$ " + (demo.command || "");
  const button = document.querySelector("[data-run]");
  if (button) {
    button.disabled = false;
    button.dataset.available = demo.available ? "yes" : "no";
    button.title = demo.available ? "" : (demo.reason || "The toolchain for this command is not installed here.");
  }
}

async function runDemo() {
  const output = document.querySelector("[data-demo-output]");
  const button = document.querySelector("[data-run]");
  if (!output) return;
  if (button) { button.disabled = true; button.textContent = "Running…"; }
  output.textContent = "running the project's own command…";
  try {
    const result = await api("/run", { method: "POST", body: "{}" });
    output.textContent = [
      "$ " + result.command,
      "",
      result.stdout || "(no standard output)",
      result.stderr ? "\n--- stderr ---\n" + result.stderr : "",
      "",
      "exit code: " + result.exit_code + " in " + result.seconds + "s",
    ].join("\n").trim();
    toast(result.exit_code === 0 ? "The command finished successfully." : "The command exited with code " + result.exit_code, result.exit_code === 0 ? "ok" : "error");
  } catch (error) {
    output.textContent = "The command could not be run: " + error.message;
    toast(error.message, "error");
  } finally {
    if (button) { button.disabled = false; button.textContent = "Run it"; }
  }
}

async function load() {
  try {
    const payload = await api("/summary");
    state.data = payload || {};
    paintStats(state.data);
    paintChart(state.data);
    renderLists(state.data);
    renderDemo(state.data.demo);
  } catch (error) {
    toast("Could not reach the console API: " + error.message, "error");
    showOffline(error.message);
  }
}

function showOffline(reason) {
  let banner = document.querySelector("[data-offline]");
  if (!banner) {
    banner = document.createElement("div");
    banner.className = "banner";
    banner.setAttribute("data-offline", "");
    const main = document.querySelector("main");
    if (main) main.prepend(banner);
  }
  banner.innerHTML =
    "<strong>The console API is not reachable.</strong> " +
    "The pages work on their own; the live panels need the small Python server in <code>web/</code>. " +
    "<code>py -3 web/server.py</code>" +
    (reason ? "<small>" + escapeHtml(reason) + "</small>" : "");
  banner.hidden = false;
}

function wireReveal() {
  const nodes = document.querySelectorAll(".reveal");
  if (!("IntersectionObserver" in window)) {
    nodes.forEach((node) => node.classList.add("in"));
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) { entry.target.classList.add("in"); observer.unobserve(entry.target); }
    });
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
  nodes.forEach((node) => observer.observe(node));
}

document.addEventListener("DOMContentLoaded", () => {
  wireReveal();
  document.querySelectorAll("[data-run]").forEach((button) => button.addEventListener("click", runDemo));
  document.querySelectorAll("[data-refresh]").forEach((button) => button.addEventListener("click", load));
  load();
});
