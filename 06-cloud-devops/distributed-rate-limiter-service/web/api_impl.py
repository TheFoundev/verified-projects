"""The API of the rate limiter: the counters, the buckets and the requests.

The numbers come from the Go code of the project. A helper program is built
from the sources of the project (limiter.go, store.go, main.go and go.mod)
with a `main` that creates a MemoryStore, counts a number of requests for one
key with Limiter.AllowAt and prints the counters, the buckets and the
Prometheus text as JSON. When the Go toolchain is not installed, the same
bucket of MemoryStore is counted here.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(tempfile.gettempdir()) / "foundev-go-helpers"
GO = shutil.which("go")

#: The helper program: it counts the requests and prints the counters as JSON.
HELPER = r'''
package main

import (
	"encoding/json"
	"flag"
	"os"
	"time"
)

func main() {
	rate := flag.Float64("limiter-rate", 2, "tokens per second")
	burst := flag.Float64("limiter-burst", 3, "the size of the bucket")
	requests := flag.Int("limiter-requests", 8, "the requests to count")
	interval := flag.Int("limiter-interval", 200, "the milliseconds between the requests")
	key := flag.String("limiter-key", "client-1", "the key of the bucket")
	flag.Parse()

	store := NewMemoryStore(*rate, *burst, 10*time.Minute)
	limiter := NewLimiter(store, *rate, *burst)
	rows := []map[string]any{}
	base := time.Now()
	for index := 0; index < *requests; index++ {
		at := base.Add(time.Duration(index) * time.Duration(*interval) * time.Millisecond)
		allowed := limiter.AllowAt(*key, at)
		rows = append(rows, map[string]any{
			"request":   index + 1,
			"allowed":   allowed,
			"remaining": limiter.RemainingAt(*key, at),
		})
	}
	buckets := []map[string]any{}
	for name, entry := range store.buckets {
		buckets = append(buckets, map[string]any{
			"key":    name,
			"tokens": entry.tokens,
			"last":   entry.last.Format(time.RFC3339Nano),
		})
	}

	json.NewEncoder(os.Stdout).Encode(map[string]any{
		"stats":      limiter.Stats(),
		"requests":   rows,
		"buckets":    buckets,
		"prometheus": limiter.Prometheus(),
		"store":      store.Name(),
	})
}
'''

#: The last count and the counts the console has made.
_STATE: dict = {}


def _sources() -> dict:
    """The sources of the project that the helper is built from."""
    files = {}
    for path in sorted(ROOT.glob("*.go")):
        if path.name.endswith("_test.go"):
            continue
        files[path.name] = path.read_text(encoding="utf-8")
    go_mod = ROOT / "go.mod"
    if go_mod.exists():
        files["go.mod"] = go_mod.read_text(encoding="utf-8")
    return files


def _build_helper() -> Path | None:
    """Build the helper program. It returns None when Go is not there."""
    if not GO:
        return None
    sources = _sources()
    key = hashlib.sha256((HELPER + json.dumps(sources, sort_keys=True)).encode()).hexdigest()[:16]
    target = CACHE / f"ratelimit-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-ratelimit-"))
    try:
        for filename, text in sources.items():
            if filename.endswith(".go"):
                # the main of the project becomes an ordinary function
                text = text.replace("func main() {", "func projectMain() {")
            (work / filename).write_text(text, encoding="utf-8")
        (work / "helper_main.go").write_text(HELPER, encoding="utf-8")
        CACHE.mkdir(parents=True, exist_ok=True)
        build = subprocess.run(
            [GO, "build", "-o", str(target), "."],
            cwd=str(work), capture_output=True, text=True, timeout=300,
        )
        if build.returncode != 0:
            return None
    except Exception:
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target if target.exists() else None


def _run_helper(rate: float, burst: float, requests: int, interval: int, key: str) -> dict | None:
    """Run the helper and read its JSON answer."""
    binary = _build_helper()
    if binary is None:
        return None
    arguments = [
        "-limiter-rate", str(rate),
        "-limiter-burst", str(burst),
        "-limiter-requests", str(requests),
        "-limiter-interval", str(interval),
        "-limiter-key", key,
    ]
    try:
        result = subprocess.run(
            [str(binary), *arguments], capture_output=True, text=True, timeout=120,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        answer = json.loads(result.stdout)
    except ValueError:
        return None
    if not isinstance(answer, dict) or answer.get("error"):
        return None
    return answer


def _count_in_python(rate: float, burst: float, requests: int, interval: int, key: str) -> dict:
    """MemoryStore.Take of store.go, mirrored.

    The bucket starts full, it is refilled with the rate on every request and
    a request is only allowed when a token is left.
    """
    base = datetime.now(timezone.utc)
    tokens = float(burst)
    last = base
    allowed = 0
    blocked = 0
    rows = []
    for index in range(requests):
        at = base + timedelta(milliseconds=index * interval)
        elapsed = (at - last).total_seconds()
        if elapsed > 0:
            tokens = min(float(burst), tokens + elapsed * rate)
            last = at
        if tokens >= 1:
            tokens -= 1
            allowed += 1
            rows.append({"request": index + 1, "allowed": True, "remaining": int(tokens)})
        else:
            blocked += 1
            rows.append({"request": index + 1, "allowed": False, "remaining": 0})
    buckets = [{"key": key, "tokens": tokens, "last": last.isoformat()}]
    stats = {
        "allowed": allowed,
        "blocked": blocked,
        "keys": 1,
        "store": 0,
        "rate": int(rate),
        "burst": int(burst),
    }
    prometheus = "\n".join([
        "# HELP ratelimit_requests_total The requests the limiter counted.",
        "# TYPE ratelimit_requests_total counter",
        f'ratelimit_requests_total{{outcome="allowed"}} {allowed}',
        f'ratelimit_requests_total{{outcome="blocked"}} {blocked}',
        "# HELP ratelimit_keys The keys the limiter knows.",
        "# TYPE ratelimit_keys gauge",
        f"ratelimit_keys 1",
    ]) + "\n"
    return {
        "stats": stats,
        "requests": rows,
        "buckets": buckets,
        "prometheus": prometheus,
        "store": "memory",
    }


def _count(rate: float, burst: float, requests: int, interval: int, key: str) -> dict:
    """Count the requests with the code of the project."""
    answer = _run_helper(rate, burst, requests, interval, key)
    if answer:
        return answer
    return _count_in_python(rate, burst, requests, interval, key)


def _last() -> dict:
    """The last count."""
    if "last" not in _STATE:
        _STATE["last"] = _count(2.0, 3.0, 8, 200, "client-1")
    return _STATE["last"]


def _metric_rows(prometheus: str) -> list:
    """The counters of the Prometheus text."""
    rows = []
    for line in (prometheus or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, value = line.rpartition(" ")
        rows.append({"Metric": name, "Value": value})
    return rows


def summary() -> dict:
    """The counters of the limiter, the buckets and the requests."""
    last = _last()
    stats = last.get("stats", {})
    buckets = last.get("buckets", [])
    requests = last.get("requests", [])
    return {
        "stats": {
            "rate": stats.get("rate", 0),
            "burst": stats.get("burst", 0),
            "allowed": stats.get("allowed", 0),
            "blocked": stats.get("blocked", 0),
            "keys": stats.get("keys", 0),
        },
        "series": [
            {"label": row["Metric"], "value": row["Value"]} for row in _metric_rows(last.get("prometheus", ""))
        ],
        "lists": {
            "requests": [
                {
                    "Request": row.get("request", 0),
                    "Allowed": "yes" if row.get("allowed") else "no",
                    "Remaining": row.get("remaining", 0),
                }
                for row in requests
            ],
            "keys": [
                {
                    "Key": bucket.get("key", ""),
                    "Tokens": round(float(bucket.get("tokens", 0)), 2),
                    "Last used": bucket.get("last", ""),
                }
                for bucket in buckets
            ],
            "metrics": _metric_rows(last.get("prometheus", "")),
        },
    }


def handle(method, path, payload, user):
    """The read and the write endpoints of the limiter."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/stats":
        stats = summary()["stats"]
        return 200, {"stats": stats}
    if method == "GET" and path == "/api/keys":
        return 200, {"keys": summary()["lists"]["keys"]}
    if method == "POST" and path == "/api/check":
        body = payload or {}
        key = str(body.get("key") or "client-1").strip() or "client-1"
        try:
            count = int(float(body.get("count") or body.get("requests") or 8))
        except (TypeError, ValueError):
            return 400, {"error": "the number of requests has to be a number"}
        try:
            interval = int(float(body.get("interval") or 200))
        except (TypeError, ValueError):
            return 400, {"error": "the interval has to be a number of milliseconds"}
        try:
            rate = float(body.get("rate") or 2)
            burst = float(body.get("burst") or 3)
        except (TypeError, ValueError):
            return 400, {"error": "the rate and the burst have to be numbers"}
        if count < 0 or interval < 0:
            return 400, {"error": "the number of requests and the interval cannot be negative"}
        answer = _count(rate, burst, count, interval, key)
        _STATE["last"] = answer
        stats = answer.get("stats", {})
        return 200, {
            "key": key,
            "allowed": stats.get("allowed", 0),
            "blocked": stats.get("blocked", 0),
            "keys": stats.get("keys", 0),
            "rate": stats.get("rate", 0),
            "burst": stats.get("burst", 0),
        }
    return 404, {"error": "not found"}
