"use strict";

const { DatabaseSync } = require("node:sqlite");

function openDatabase(file) {
  const db = new DatabaseSync(file);
  db.exec(`
    CREATE TABLE IF NOT EXISTS monitors (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      url TEXT NOT NULL,
      interval_seconds INTEGER NOT NULL DEFAULT 60,
      enabled INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS checks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      monitor_id INTEGER NOT NULL,
      status TEXT NOT NULL,
      code INTEGER,
      latency_ms INTEGER NOT NULL DEFAULT 0,
      message TEXT,
      checked_at INTEGER NOT NULL
    );
  `);

  const insertMonitor = db.prepare(
    "INSERT INTO monitors (name, url, interval_seconds, enabled) VALUES (?, ?, ?, 1)",
  );
  const selectMonitor = db.prepare("SELECT * FROM monitors WHERE id = ?");
  const selectMonitors = db.prepare("SELECT * FROM monitors ORDER BY id");
  const deleteMonitor = db.prepare("DELETE FROM monitors WHERE id = ?");
  const insertCheck = db.prepare(
    "INSERT INTO checks (monitor_id, status, code, latency_ms, message, checked_at) VALUES (?, ?, ?, ?, ?, ?)",
  );
  const selectChecks = db.prepare(
    "SELECT * FROM checks WHERE monitor_id = ? ORDER BY id DESC LIMIT ?",
  );

  const store = {
    createMonitor({ name, url, intervalSeconds = 60 }) {
      if (!name || !url) {
        throw new Error("name and url are required");
      }
      const seconds = Math.max(5, Number(intervalSeconds) || 60);
      const result = insertMonitor.run(String(name), String(url), seconds);
      return store.getMonitor(Number(result.lastInsertRowid));
    },
    getMonitor(id) {
      return selectMonitor.get(Number(id)) || null;
    },
    listMonitors() {
      return selectMonitors.all();
    },
    deleteMonitor(id) {
      return Number(deleteMonitor.run(Number(id)).changes) > 0;
    },
    recordCheck({ monitorId, status, code = null, latencyMs = 0, message = "" }) {
      insertCheck.run(
        Number(monitorId),
        String(status),
        code === null || code === undefined ? null : Number(code),
        Number(latencyMs) || 0,
        String(message || ""),
        Date.now(),
      );
    },
    recentChecks(monitorId, limit = 20) {
      return selectChecks.all(Number(monitorId), Number(limit));
    },
    summary() {
      return store.listMonitors().map((monitor) => {
        const [last] = selectChecks.all(monitor.id, 1);
        return {
          ...monitor,
          status: last ? last.status : "unknown",
          lastCheckedAt: last ? last.checked_at : null,
          latencyMs: last ? last.latency_ms : null,
        };
      });
    },
    close() {
      db.close();
    },
  };

  return store;
}

module.exports = { openDatabase };
