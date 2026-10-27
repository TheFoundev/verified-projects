"""The API of the span store: the spans, the traces and the latency of every service.

The numbers come from the Go code of the project. A helper program is built
from the sources of the project (tracing.go, main.go and go.mod) with a
`main` that fills a Store with the sample spans of main.go, groups the spans
into traces with Store.Traces, computes the latency of every service with
Store.Stats and the percentiles with Percentile, and prints the result as
JSON. When the Go toolchain is not installed, the sample spans are read from
main.go and the same computation is done here.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(tempfile.gettempdir()) / "foundev-go-helpers"
GO = shutil.which("go")

#: How long an answer of the helper is reused.
TTL = 20.0

#: The helper program: it stores the spans and prints the store as JSON.
HELPER = r'''
package main

import (
	"encoding/json"
	"os"
)

func main() {
	var input struct {
		Spans []Span `json:"spans"`
	}
	_ = json.NewDecoder(os.Stdin).Decode(&input)

	store := NewStore()
	if len(input.Spans) == 0 {
		store.Add(sampleSpans()...)
	} else {
		store.Add(input.Spans...)
	}

	spans := store.all()
	values := []float64{}
	errors := 0
	for _, span := range spans {
		values = append(values, float64(span.Duration.Milliseconds()))
		if span.Status == "error" {
			errors++
		}
	}
	percentiles := []map[string]any{}
	for _, p := range []float64{50, 90, 95, 99} {
		percentiles = append(percentiles, map[string]any{
			"percentile": p,
			"ms":         Percentile(values, p),
		})
	}

	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(map[string]any{
		"spans":       spans,
		"traces":      store.Traces(),
		"stats":       store.Stats(),
		"percentiles": percentiles,
		"errors":      errors,
	})
}
'''

#: The sample spans of main.go, read when Go is not installed.
SPAN_PATTERN = re.compile(
    r'\{TraceID: "([^"]*)", SpanID: "([^"]*)"(?:, ParentID: "([^"]*)")?, '
    r'Service: "([^"]*)", Operation: "([^"]*)", Start: ([^,]+), '
    r"Duration: (\d+) \* time\.(\w+), Status: \"([^\"]*)\"\}"
)

#: How long a Go duration is in milliseconds.
UNITS = {"Nanosecond": 0.000001, "Microsecond": 0.001, "Millisecond": 1.0,
         "Second": 1000.0, "Minute": 60000.0, "Hour": 3600000.0}

#: The spans of the store, the campaigns of the console and the cache.
_STATE: dict = {"spans": None}
_ANSWER: dict = {"at": 0.0, "key": None, "data": None}


# --------------------------------------------------------------------------
# the helper
# --------------------------------------------------------------------------

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
    target = CACHE / f"tracing-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-tracing-"))
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


def _run_helper(spans: list) -> dict | None:
    """Store the spans with the code of the project and read the answer."""
    binary = _build_helper()
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [str(binary)], input=json.dumps({"spans": spans}),
            capture_output=True, text=True, timeout=120,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        answer = json.loads(result.stdout)
    except ValueError:
        return None
    return answer if isinstance(answer, dict) else None


# --------------------------------------------------------------------------
# the fallback: the same computation, in Python
# --------------------------------------------------------------------------

def _sample_spans() -> list:
    """The sample spans of main.go, read from the source."""
    text = (ROOT / "main.go").read_text(encoding="utf-8")
    epoch = re.search(r"time\.Unix\((\d+)", text)
    base = int(epoch.group(1)) if epoch else 0
    spans = []
    for match in SPAN_PATTERN.finditer(text):
        trace_id, span_id, parent_id, service, operation, start, amount, unit, status = match.groups()
        offset = 0.0
        addition = re.search(r"Add\((?:(\d+) \* time\.(\w+)|time\.(\w+))\)", start)
        if addition:
            if addition.group(1):
                offset = float(addition.group(1)) * UNITS.get(addition.group(2), 1.0)
            else:
                offset = UNITS.get(addition.group(3), 1.0)
        spans.append({
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_id": parent_id or "",
            "service": service,
            "operation": operation,
            "start": _stamp(base, offset),
            # a time.Duration is written in nanoseconds
            "duration": int(round(float(amount) * UNITS.get(unit, 1.0) * 1e6)),
            "status": status,
        })
    return spans


def _stamp(base: int, offset: float) -> str:
    """A start of a span, as the RFC3339 string of a time.Time."""
    moment = datetime.fromtimestamp(base + offset / 1000.0, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seconds(stamp: str) -> float:
    """The epoch seconds of a start of a span."""
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _percentile(values: list, p: float) -> float:
    """Percentile of tracing.go, mirrored."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(p / 100 * float(len(ordered) - 1))
    return ordered[index]


def _fallback(spans: list) -> dict:
    """Traces, Stats and Percentile of tracing.go, mirrored."""
    if not spans:
        spans = _sample_spans()
    traces = {}
    for span in spans:
        traces.setdefault(span["trace_id"], []).append(span)
    trace_rows = []
    for trace_id, items in sorted(traces.items()):
        starts = [_seconds(item["start"]) for item in items]
        ends = [start + item["duration"] / 1e9 for start, item in zip(starts, items)]
        errors = sum(1 for item in items if item["status"] == "error")
        root = ""
        for item in items:
            if not item["parent_id"]:
                root = item["span_id"]
        trace_rows.append({
            "trace_id": trace_id,
            "spans": items,
            "duration": int(round((max(ends) - min(starts)) * 1e9)),
            "errors": errors,
            "root": root,
        })
    services = {}
    for span in spans:
        services.setdefault(span["service"], []).append(span)
    stat_rows = []
    for service, items in sorted(services.items()):
        values = [float(item["duration"] // 1_000_000) for item in items]
        stat_rows.append({
            "service": service,
            "spans": len(items),
            "p50_ms": _percentile(values, 50),
            "p95_ms": _percentile(values, 95),
            "errors": sum(1 for item in items if item["status"] == "error"),
        })
    values = [float(span["duration"] // 1_000_000) for span in spans]
    percentiles = [
        {"percentile": p, "ms": _percentile(values, p)} for p in (50, 90, 95, 99)
    ]
    return {
        "spans": spans,
        "traces": trace_rows,
        "stats": stat_rows,
        "percentiles": percentiles,
        "errors": sum(1 for span in spans if span["status"] == "error"),
    }


# --------------------------------------------------------------------------
# the state of the console
# --------------------------------------------------------------------------

def _spans() -> list:
    """The spans of the store."""
    if _STATE["spans"] is None:
        answer = _run_helper([]) or _fallback([])
        _STATE["spans"] = answer.get("spans", [])
    return _STATE["spans"]


def _answer(force: bool = False) -> dict:
    """The answer of the store, computed by the code of the project."""
    spans = _spans()
    key = json.dumps(spans, sort_keys=True)
    now = time.time()
    if (not force and _ANSWER["data"] is not None and _ANSWER["key"] == key
            and now - _ANSWER["at"] < TTL):
        return _ANSWER["data"]
    answer = _run_helper(spans) or _fallback(spans)
    _ANSWER.update({"at": now, "key": key, "data": answer})
    return answer


def _milliseconds(value) -> float:
    """A Go duration in nanoseconds, in milliseconds."""
    try:
        return round(float(value) / 1e6, 3)
    except (TypeError, ValueError):
        return 0.0


def _stat_rows(answer: dict) -> list:
    """The per service latency, as the console shows it."""
    return [
        {
            "Service": row.get("service", ""),
            "Spans": row.get("spans", 0),
            "p50 (ms)": row.get("p50_ms", 0),
            "p95 (ms)": row.get("p95_ms", 0),
            "Errors": row.get("errors", 0),
        }
        for row in answer.get("stats", [])
    ]


def _trace_rows(answer: dict) -> list:
    """The traces, as the console shows them."""
    return [
        {
            "Trace": row.get("trace_id", ""),
            "Spans": len(row.get("spans", []) or []),
            "Duration (ms)": _milliseconds(row.get("duration", 0)),
            "Errors": row.get("errors", 0),
            "Root": row.get("root", ""),
        }
        for row in answer.get("traces", [])
    ]


def _span_rows(answer: dict) -> list:
    """The spans, as the console shows them."""
    rows = []
    for span in answer.get("spans", []):
        rows.append({
            "Trace": span.get("trace_id", ""),
            "Span": span.get("span_id", ""),
            "Parent": span.get("parent_id", ""),
            "Service": span.get("service", ""),
            "Operation": span.get("operation", ""),
            "Duration (ms)": _milliseconds(span.get("duration", 0)),
            "Status": span.get("status", ""),
        })
    return rows


def _percentile_rows(answer: dict) -> list:
    """The percentiles of all the spans."""
    return [
        {"Percentile": f"p{int(row.get('percentile', 0))}", "Milliseconds": row.get("ms", 0)}
        for row in answer.get("percentiles", [])
    ]


def summary() -> dict:
    """The spans, the traces and the latency of every service."""
    answer = _answer()
    stats = answer.get("stats", [])
    percentiles = answer.get("percentiles", [])
    p50 = 0.0
    p95 = 0.0
    for row in percentiles:
        if int(row.get("percentile", 0)) == 50:
            p50 = row.get("ms", 0)
        if int(row.get("percentile", 0)) == 95:
            p95 = row.get("ms", 0)
    return {
        "stats": {
            "spans": len(answer.get("spans", [])),
            "traces": len(answer.get("traces", [])),
            "services": len(stats),
            "errors": answer.get("errors", 0),
            "p50": p50,
            "p95": p95,
        },
        "series": [
            {"label": row.get("service", ""), "value": row.get("spans", 0)} for row in stats
        ],
        "lists": {
            "services": _stat_rows(answer),
            "traces": _trace_rows(answer),
            "spans": _span_rows(answer),
            "percentiles": _percentile_rows(answer),
        },
    }


# --------------------------------------------------------------------------
# the endpoints
# --------------------------------------------------------------------------

def _span_from_form(body: dict) -> dict | None:
    """Build a span from the fields of the form."""
    trace_id = str(body.get("trace_id") or "").strip()
    service = str(body.get("service") or "").strip()
    if not trace_id or not service:
        return None
    try:
        milliseconds = float(str(body.get("duration_ms") or "0").strip() or "0")
    except ValueError:
        return None
    return {
        "trace_id": trace_id,
        "span_id": str(body.get("span_id") or "").strip(),
        "parent_id": str(body.get("parent_id") or "").strip(),
        "service": service,
        "operation": str(body.get("operation") or "").strip(),
        "start": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "duration": milliseconds * 1e6,
        "status": str(body.get("status") or "ok").strip() or "ok",
    }


def handle(method, path, payload, user):
    """The endpoints of the span store."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/stats":
        return 200, {"stats": _stat_rows(_answer())}
    if method == "GET" and path == "/api/traces":
        return 200, {"traces": _trace_rows(_answer())}
    if method == "GET" and path == "/api/spans":
        return 200, {"spans": _span_rows(_answer())}
    if method == "GET" and path == "/api/percentiles":
        return 200, {"percentiles": _percentile_rows(_answer())}
    if method == "POST" and path == "/api/ingest":
        body = payload or {}
        incoming = []
        if isinstance(body, list):
            incoming = [item for item in body if isinstance(item, dict)]
        elif isinstance(body, dict):
            raw = body.get("spans")
            if isinstance(raw, list):
                incoming = [item for item in raw if isinstance(item, dict)]
            elif isinstance(raw, str) and raw.strip():
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = None
                if isinstance(parsed, list):
                    incoming = [item for item in parsed if isinstance(item, dict)]
                elif isinstance(parsed, dict):
                    incoming = [parsed]
            else:
                span = _span_from_form(body)
                if span:
                    incoming = [span]
        if not incoming:
            return 400, {"error": "no span was sent"}
        spans = _spans() + incoming
        _STATE["spans"] = spans
        answer = _answer(force=True)
        return 200, {
            "accepted": len(incoming),
            "spans": len(answer.get("spans", [])),
            "traces": len(answer.get("traces", [])),
        }
    return 404, {"error": "not found"}
