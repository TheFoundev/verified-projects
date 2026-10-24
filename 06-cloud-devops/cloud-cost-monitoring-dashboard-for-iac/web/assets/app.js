/* The runtime of the cloud-cost-monitoring-dashboard-for-iac site.
   It fetches from the API of the project, renders the panels and keeps the
   session of the signed-in user. */

const API = "/api";
const state = { user: null, data: {} };

/** fetch + JSON, with a readable error. */
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

/** A toast in the corner. */
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

/** Fill the elements marked with data-stat from the API answer. */
function paintStats(payload) {
  const values = (payload && payload.stats) || payload || {};
  document.querySelectorAll("[data-stat]").forEach((node) => {
    const value = values[node.dataset.stat];
    if (value !== undefined && value !== null) node.textContent = value;
  });
}

/** Draw a small bar chart into every canvas.chart. */
function paintChart(payload) {
  const series = (payload && payload.series) || [];
  document.querySelectorAll("svg.chart").forEach((svg) => {
    if (!series.length) {
      svg.innerHTML = '<text x="320" y="90" text-anchor="middle" font-size="13" fill="currentColor" opacity=".55">No series is available for this view.</text>';
      return;
    }
    const width = 640, height = 190, gap = 8;
    const max = Math.max(...series.map((point) => Number(point.value) || 0), 1);
    const bar = (width - gap * (series.length - 1)) / series.length;
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("role", "img");
    const label = svg.dataset.chartLabel || "Series";
    svg.setAttribute("aria-label", label + ": " + series.map((point) => (point.label || "") + " " + point.value).join(", "));
    const marks = series.map((point, index) => {
      const value = Number(point.value) || 0;
      const h = Math.max(3, (value / max) * (height - 58));
      const x = index * (bar + gap);
      const cx = x + bar / 2;
      const text = String(point.label ?? "");
      const short = text.length > 11 ? text.slice(0, 10) + "\u2026" : text;
      const shown = value >= 1000 ? (value / 1000).toFixed(1) + "k" : String(Math.round(value * 100) / 100);
      return '<rect class="bar" x="' + x + '" y="' + (height - h - 30) + '" width="' + bar +
        '" height="' + h + '" rx="3"><title>' + escapeHtml(text) + ": " + value + "</title></rect>" +
        '<text x="' + cx + '" y="' + (height - h - 36) + '" text-anchor="middle" font-size="10" fill="currentColor" opacity=".75">' + escapeHtml(shown) + "</text>" +
        '<text x="' + cx + '" y="' + (height - 12) + '" text-anchor="middle" font-size="10" fill="currentColor" opacity=".6">' + escapeHtml(short) + "</text>";
    }).join("");
    svg.innerHTML = marks + '<line class="axis" x1="0" y1="' + (height - 29) + '" x2="' + width + '" y2="' + (height - 29) + '"></line>';
  });
}

/** Load the panels of the page. */
async function load() {
  try {
    const payload = await api("/summary");
    state.data = payload || {};
    paintStats(state.data);
    paintChart(state.data);
    const lists = (state.data.lists) || state.data || {};
    document.querySelectorAll("[data-list]").forEach((node) => {
      const listKey = node.dataset.list;
      const rows = (lists[listKey] || state.data[listKey] || []);
      if (!rows.length) {
        const colSpan = node.querySelectorAll("thead th").length || 4;
        node.innerHTML = '<tr><td colspan="' + colSpan + '" class="muted">Nothing yet.</td></tr>';
        return;
      }
      node.innerHTML = rows.map((row) => {
        const cells = Object.values(row).map((cell) => "<td>" + escapeHtml(String(cell)) + "</td>").join("");
        return "<tr>" + cells + "</tr>";
      }).join("");
    });
  } catch (error) {
    toast("Could not reach the API: " + error.message, "error");
    showOffline(error.message);
  }
}

/** The banner that explains how to start the API. */
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
    "<strong>The API is not reachable.</strong> " +
    "The pages work on their own, the live panels need the server of the project. " +
    "<code>python3 web/server.py --port 8000</code>" +
    (reason ? "<small>" + reason + "</small>" : "");
  banner.hidden = false;
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

/** The sign-in form of the console and of the home page. */
function wireLogin() {
  const form = document.querySelector("[data-login]");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form).entries());
    try {
      const answer = await api("/session", { method: "POST", body: JSON.stringify(data) });
      state.user = answer.user || { name: data.username };
      toast("Signed in as " + (state.user.name || data.username));
      document.querySelectorAll("[data-when-signed-out]").forEach((n) => (n.hidden = true));
      document.querySelectorAll("[data-when-signed-in]").forEach((n) => (n.hidden = false));
      load();
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

/** Sign out. */
function wireLogout() {
  document.querySelectorAll("[data-logout]").forEach((button) => {
    button.addEventListener("click", async () => {
      try { await api("/session", { method: "DELETE" }); } catch (error) { /* ignore */ }
      toast("Signed out");
      location.reload();
    });
  });
}

/** Reveal the sections as they come into view. */
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
  }, { rootMargin: "0px 0px -8% 0px", threshold: .08 });
  nodes.forEach((node) => observer.observe(node));
}

/** The tabs of the console. */
function wireTabs() {
  document.querySelectorAll("[data-tabs]").forEach((group) => {
    const buttons = group.querySelectorAll("button");
    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        buttons.forEach((other) => other.setAttribute("aria-selected", String(other === button)));
        document.querySelectorAll("[data-tab]").forEach((panel) => {
          panel.hidden = panel.dataset.tab !== button.dataset.tab;
        });
      });
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  wireLogin();
  wireLogout();
  wireReveal();
  wireTabs();
  load();
});
