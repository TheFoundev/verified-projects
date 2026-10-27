"use strict";

async function checkOnce(monitor, fetchImpl = fetch) {
  const started = Date.now();
  try {
    const response = await fetchImpl(monitor.url, {
      method: "GET",
      redirect: "follow",
      signal: AbortSignal.timeout(5000),
    });
    return {
      status: response.ok ? "up" : "down",
      code: response.status,
      latencyMs: Date.now() - started,
      message: response.ok ? "" : `HTTP ${response.status}`,
    };
  } catch (error) {
    return {
      status: "down",
      code: null,
      latencyMs: Date.now() - started,
      message: String((error && error.message) || error),
    };
  }
}

/**
 * The state a monitor was in the last time it was checked.
 *
 * The state is remembered, so a notification is only sent when the state
 * changes and not on every check.
 */
function previousStatus(store, monitorId) {
  const checks = store.recentChecks(monitorId, 1);
  if (!checks.length) return null;
  return checks[0].status;
}

/**
 * The notifications that have to go out.
 *
 * A monitor that goes down is announced, and so is a monitor that comes
 * back. A monitor that stays down is not announced again.
 */
function notificationsFor(store, monitor, result) {
  const before = previousStatus(store, monitor.id);
  if (before === result.status) {
    return [];
  }
  if (result.status === "down") {
    return [{ monitor, status: "down", message: `${monitor.name} is down: ${result.message}` }];
  }
  if (before === "down" || before === "late") {
    return [{ monitor, status: "up", message: `${monitor.name} is up again.` }];
  }
  return [];
}

/**
 * Send a notification.
 *
 * The webhook is called with the text of the notification, and the mail is
 * written to the log of the service when there is no mail server.
 */
async function notify(notification, options = {}) {
  const webhook = options.webhook || process.env.UPTIME_WEBHOOK_URL;
  const send = options.send || (typeof fetch === "function" ? fetch : null);

  if (webhook && send) {
    try {
      const response = await send(webhook, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(notification),
      });
      return { sent: response.ok, channel: "webhook" };
    } catch (error) {
      return { sent: false, channel: "webhook", error: String(error && error.message) };
    }
  }

  if (options.log !== false && typeof console !== "undefined") {
    console.log(`[UPTIME ALERT] ${notification.message}`);
  }
  return { sent: false, channel: "log" };
}

async function runOnce(store, fetchImpl = fetch, options = {}) {
  const results = [];
  const sent = [];

  for (const monitor of store.listMonitors()) {
    if (!monitor.enabled) {
      continue;
    }
    const result = await checkOnce(monitor, fetchImpl);
    store.recordCheck({ monitorId: monitor.id, ...result });
    results.push({ monitor, result });

    for (const notification of notificationsFor(store, monitor, result)) {
      const answer = await notify(notification, options);
      sent.push({ ...notification, ...answer });
    }
  }
  return { results, notifications: sent };
}

function startScheduler(store, intervalMs = 60000, fetchImpl = fetch) {
  const timer = setInterval(() => {
    runOnce(store, fetchImpl).catch(() => {});
  }, Math.max(1000, Number(intervalMs)));
  if (typeof timer.unref === "function") {
    timer.unref();
  }
  return timer;
}

module.exports = {
  checkOnce,
  runOnce,
  startScheduler,
  notificationsFor,
  notify,
  previousStatus,
};
