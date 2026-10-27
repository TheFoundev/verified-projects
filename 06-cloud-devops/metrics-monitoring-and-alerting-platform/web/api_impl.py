"""The API of the metrics store: samples in a window, rules that fire.

The numbers come from the Go code of the project. The sample series and the
alert rules are read from main.go, the way they are written there, and a
helper program is built from the sources of the project (metrics.go, main.go
and go.mod) with a `main` that fills a Store with those samples, asks
Store.Rate, Store.Sum and Store.Average for the windows, calls Evaluate for
the rules and writes Store.Prometheus. The helper prints the answer as JSON.

When the Go toolchain is not installed, the same computation is done here in
Python, with the same windows, the same thresholds and the same text format
as Store.Prometheus, so the numbers do not change.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(tempfile.gettempdir()) / "foundev-go-helpers"
GO = shutil.which("go")

#: How long an answer of the helper is reused.
TTL = 20.0

#: The window the console shows the metrics over, in seconds.
WINDOW = 600

#: The window Store.Prometheus uses for the rate of a metric, in seconds.
PROMETHEUS_WINDOW = 300

#: The helper program: it fills the store and prints everything as JSON.
HELPER = r'''
package main

import (
	"encoding/json"
	"os"
	"sort"
	"time"
)

type inSample struct {
	Metric string            `json:"metric"`
	Labels map[string]string `json:"labels"`
	Value  float64           `json:"value"`
	At     int64             `json:"at"`
}

type inRule struct {
	Name      string  `json:"name"`
	Metric    string  `json:"metric"`
	Threshold float64 `json:"threshold"`
	For       int64   `json:"for"`
	Mode      string  `json:"mode"`
}

func main() {
	var input struct {
		Samples []inSample `json:"samples"`
		Rules   []inRule   `json:"rules"`
		Now     int64      `json:"now"`
	}
	if err := json.NewDecoder(os.Stdin).Decode(&input); err != nil {
		os.Exit(1)
	}

	store := NewStore()
	for _, sample := range input.Samples {
		store.Add(Sample{
			Metric: sample.Metric, Labels: sample.Labels,
			Value: sample.Value, At: time.Unix(sample.At, 0),
		})
	}
	now := time.Unix(input.Now, 0)

	rules := []Rule{}
	for _, rule := range input.Rules {
		rules = append(rules, Rule{
			Name: rule.Name, Metric: rule.Metric, Threshold: rule.Threshold,
			For: time.Duration(rule.For) * time.Second, Mode: rule.Mode,
		})
	}

	names := []string{}
	seen := map[string]bool{}
	for _, sample := range input.Samples {
		if !seen[sample.Metric] {
			seen[sample.Metric] = true
			names = append(names, sample.Metric)
		}
	}
	sort.Strings(names)

	window := 10 * time.Minute
	metrics := []map[string]any{}
	for _, name := range names {
		metrics = append(metrics, map[string]any{
			"metric":  name,
			"samples": len(store.window(name, window, now)),
			"rate":    store.Rate(name, window, now),
			"sum":     store.Sum(name, window, now),
			"average": store.Average(name, window, now),
		})
	}

	outSamples := []map[string]any{}
	for _, sample := range input.Samples {
		outSamples = append(outSamples, map[string]any{
			"metric": sample.Metric,
			"labels": sample.Labels,
			"value":  sample.Value,
			"at":     time.Unix(sample.At, 0).Format(time.RFC3339),
		})
	}

	outRules := []map[string]any{}
	for _, rule := range rules {
		outRules = append(outRules, map[string]any{
			"name":      rule.Name,
			"metric":    rule.Metric,
			"threshold": rule.Threshold,
			"for":       int64(rule.For / time.Second),
			"mode":      rule.Mode,
		})
	}

	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(map[string]any{
		"samples":    outSamples,
		"metrics":    metrics,
		"rules":      outRules,
		"alerts":     Evaluate(store, rules, now),
		"prometheus": store.Prometheus(now),
	})
}
'''

#: The Go durations, in seconds.
UNITS = {
    "Nanosecond": 1e-9, "Microsecond": 1e-6, "Millisecond": 1e-3,
    "Second": 1.0, "Minute": 60.0, "Hour": 3600.0,
}

#: The sample literals of main.go.
SAMPLE_PATTERN = re.compile(
    r'Sample\{Metric:\s*"(?P<metric>[^"]*)",\s*'
    r'Labels:\s*map\[string\]string\{(?P<labels>[^}]*)\},\s*'
    r'Value:\s*(?P<value>[^,]*),\s*At:\s*(?P<at>[^}]*)\}'
)

#: The rule literals of main.go.
RULE_PATTERN = re.compile(r"Rule\{\{?(?P<body>[^{}]*)\}\}?")


# --------------------------------------------------------------------------
# the data of the project, read from main.go
# --------------------------------------------------------------------------

def _duration_seconds(expression: str) -> float:
    """A Go duration expression in seconds, as metrics.go reads it."""
    match = re.search(r"time\.(Nanosecond|Microsecond|Millisecond|Second|Minute|Hour)", expression)
    if not match:
        raise ValueError(f"no time unit in {expression!r}")
    unit = UNITS[match.group(1)]
    product = 1
    for number in re.findall(r"\d+", expression):
        product *= int(number)
    return product * unit


def _offset_seconds(expression: str, index: int) -> float:
    """The offset of a sample of main.go, in seconds after the base."""
    match = re.search(r"time\.(Nanosecond|Microsecond|Millisecond|Second|Minute|Hour)", expression)
    if not match:
        raise ValueError(f"no time unit in {expression!r}")
    unit = UNITS[match.group(1)]
    product = 1
    for number in re.findall(r"\d+", expression):
        product *= int(number)
    return index * product * unit


def _value(expression: str, index: int) -> float:
    """The value of a sample of main.go."""
    inner = re.sub(r"float64\(([^()]*)\)", r"\1", expression).strip()
    inner = inner.replace("index", "INDEX")
    if not re.fullmatch(r"[0-9+\-*/() ]+", inner.replace("INDEX", "1")):
        raise ValueError(f"cannot read {expression!r}")
    return float(eval(inner, {"__builtins__": {}}, {"INDEX": index}))


def _labels(text: str) -> dict:
    """The labels of a sample of main.go."""
    labels = {}
    for key, value in re.findall(r'"([^"]*)"\s*:\s*"([^"]*)"', text):
        labels[key] = value
    return labels


def _main_source() -> str:
    """The source of main.go."""
    return (ROOT / "main.go").read_text(encoding="utf-8")


def _sample_data() -> tuple:
    """The sample series and the base time of main.go."""
    text = _main_source()
    series = text[text.index("func sampleSeries()"):text.index("func main()")]
    base_match = re.search(r"time\.Unix\((\d+)", series)
    base = int(base_match.group(1)) if base_match else 0
    loop = re.search(r"for\s+index\s*:=\s*0;\s*index\s*<\s*(\d+)", series)
    rounds = int(loop.group(1)) if loop else 0
    literals = list(SAMPLE_PATTERN.finditer(series))
    samples = []
    for index in range(rounds):
        for literal in literals:
            samples.append({
                "metric": literal.group("metric"),
                "labels": _labels(literal.group("labels")),
                "value": _value(literal.group("value"), index),
                "at": base + _offset_seconds(literal.group("at"), index),
            })
    return samples, base


def _rule_data() -> list:
    """The alert rules of main.go."""
    text = _main_source()
    rules = []
    for match in RULE_PATTERN.finditer(text):
        body = match.group("body")
        name = re.search(r'Name:\s*"([^"]*)"', body)
        metric = re.search(r'Metric:\s*"([^"]*)"', body)
        threshold = re.search(r"Threshold:\s*(-?[0-9.]+)", body)
        duration = re.search(r"For:\s*([^,]+)", body)
        mode = re.search(r'Mode:\s*"([^"]*)"', body)
        if not (name and metric and threshold and duration and mode):
            continue
        rules.append({
            "name": name.group(1),
            "metric": metric.group(1),
            "threshold": float(threshold.group(1)),
            "for": int(_duration_seconds(duration.group(1).strip())),
            "mode": mode.group(1),
        })
    return rules


def _evaluation_now() -> float:
    """The moment Evaluate looks at, as main.go writes it."""
    _samples, base = _sample_data()
    return _now(base)


def _now(base: int) -> float:
    """The moment Evaluate looks at, as main.go writes it."""
    text = _main_source()
    match = re.search(r"time\.Unix\((\d+),\s*0\)\.Add\(([^)]*)\)", text)
    if not match:
        return float(base)
    return float(int(match.group(1)) + _duration_seconds(match.group(2).strip()))


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
    target = CACHE / f"metrics-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-metrics-"))
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


def _run_helper(samples: list, rules: list, now: float) -> dict | None:
    """Ask the code of the project for the numbers."""
    binary = _build_helper()
    if binary is None:
        return None
    payload = {
        "samples": [
            {
                "metric": sample["metric"], "labels": sample.get("labels", {}),
                "value": sample["value"], "at": int(sample["at"]),
            }
            for sample in samples
        ],
        "rules": rules,
        "now": int(now),
    }
    try:
        result = subprocess.run(
            [str(binary)], input=json.dumps(payload),
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

def _window(samples: list, metric: str, window: float, now: float) -> list:
    """The samples of a metric in a window, as Store.window returns them."""
    cutoff = now - window
    selected = [
        sample for sample in samples
        if sample["metric"] == metric and cutoff <= sample["at"] <= now
    ]
    selected.sort(key=lambda sample: sample["at"])
    return selected


def _rate(samples: list, metric: str, window: float, now: float) -> float:
    """Store.Rate, mirrored."""
    selected = _window(samples, metric, window, now)
    if len(selected) < 2:
        return 0.0
    first, last = selected[0], selected[-1]
    seconds = last["at"] - first["at"]
    if seconds <= 0:
        return 0.0
    return (last["value"] - first["value"]) / seconds


def _rate_locked(samples: list, metric: str, window: float, now: float) -> float:
    """Store.rateLocked, mirrored."""
    selected = _window(samples, metric, window, now)
    if len(selected) < 2:
        return 0.0
    first, last = selected[0], selected[-1]
    seconds = last["at"] - first["at"]
    if seconds <= 0:
        return 0.0
    return float(len(selected) - 1) / seconds


def _sum(samples: list, metric: str, window: float, now: float) -> float:
    """Store.Sum, mirrored."""
    return float(sum(sample["value"] for sample in _window(samples, metric, window, now)))


def _average(samples: list, metric: str, window: float, now: float) -> float:
    """Store.Average, mirrored."""
    selected = _window(samples, metric, window, now)
    if not selected:
        return 0.0
    return _sum(samples, metric, window, now) / float(len(selected))


def _evaluate(samples: list, rules: list, now: float) -> list:
    """Evaluate, mirrored."""
    alerts = []
    for rule in rules:
        window = float(rule["for"])
        value = _average(samples, rule["metric"], window, now)
        if rule.get("mode") == "rate":
            value = _rate(samples, rule["metric"], window, now)
        if value <= rule["threshold"]:
            continue
        selected = _window(samples, rule["metric"], window, now)
        if len(selected) < 2:
            continue
        alerts.append({
            "rule": rule["name"], "metric": rule["metric"], "value": value,
            "threshold": rule["threshold"], "since": _stamp(selected[0]["at"]),
        })
    return alerts


def _stamp(epoch: float) -> str:
    """A time as the RFC3339 string of Go."""
    moment = datetime.fromtimestamp(epoch).astimezone()
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + moment.strftime("%z")[:3] + ":" + moment.strftime("%z")[3:]


def _prometheus(samples: list, now: float) -> str:
    """Store.Prometheus, mirrored."""
    lines = [
        "# HELP metrics_samples_total The samples that were taken in.",
        "# TYPE metrics_samples_total counter",
        f"metrics_samples_total {len(samples)}",
    ]
    metrics = []
    for sample in samples:
        if sample["metric"] not in metrics:
            metrics.append(sample["metric"])
    for metric in sorted(metrics):
        rate = _rate_locked(samples, metric, PROMETHEUS_WINDOW, now)
        lines.append(f"# HELP metrics_rate_{metric} The rate of the metric over the window.")
        lines.append(f"# TYPE metrics_rate_{metric} gauge")
        lines.append(f"metrics_rate_{metric} {rate:.6f}")
    return "\n".join(lines) + "\n"


def _sorted_prometheus(text: str) -> str:
    """The blocks of the exporter, in the order of the metric names."""
    lines = [line for line in text.split("\n") if line]
    header, blocks = [], []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("# HELP metrics_rate_"):
            block = [line]
            index += 1
            while index < len(lines) and lines[index].startswith("#"):
                block.append(lines[index])
                index += 1
            if index < len(lines):
                block.append(lines[index])
                index += 1
            blocks.append(block)
            continue
        header.append(line)
        index += 1
    blocks.sort(key=lambda block: block[0].split(" ")[2])
    out = header + [line for block in blocks for line in block]
    return "\n".join(out) + "\n"


def _fallback(samples: list, rules: list, now: float) -> dict:
    """The answer of the helper, computed in Python."""
    metrics = []
    names = []
    for sample in samples:
        if sample["metric"] not in names:
            names.append(sample["metric"])
    for name in sorted(names):
        metrics.append({
            "metric": name,
            "samples": len(_window(samples, name, WINDOW, now)),
            "rate": _rate(samples, name, WINDOW, now),
            "sum": _sum(samples, name, WINDOW, now),
            "average": _average(samples, name, WINDOW, now),
        })
    return {
        "samples": [
            {
                "metric": sample["metric"], "labels": sample.get("labels", {}),
                "value": sample["value"], "at": _stamp(sample["at"]),
            }
            for sample in samples
        ],
        "metrics": metrics,
        "rules": rules,
        "alerts": _evaluate(samples, rules, now),
        "prometheus": _prometheus(samples, now),
    }


# --------------------------------------------------------------------------
# the state of the console
# --------------------------------------------------------------------------

#: The samples that were ingested through the API.
_INGESTED: list = []
_ANSWER: dict = {"at": 0.0, "key": None, "data": None}


def _base_samples() -> tuple:
    """The samples and the rules of main.go."""
    samples, base = _sample_data()
    return samples, _rule_data(), _now(base)


def _answer(force: bool = False) -> dict:
    """The answer of the store, computed by the code of the project."""
    samples, rules, now = _base_samples()
    samples = samples + list(_INGESTED)
    key = json.dumps([samples, rules, now], sort_keys=True)
    moment = time.time()
    if (not force and _ANSWER["data"] is not None and _ANSWER["key"] == key
            and moment - _ANSWER["at"] < TTL):
        return _ANSWER["data"]
    answer = _run_helper(samples, rules, now) or _fallback(samples, rules, now)
    answer["prometheus"] = _sorted_prometheus(answer.get("prometheus", ""))
    _ANSWER.update({"at": moment, "key": key, "data": answer})
    return answer


# --------------------------------------------------------------------------
# the rows of the console
# --------------------------------------------------------------------------

def _metric_rows(answer: dict) -> list:
    """The metrics of the store, as the console shows them."""
    return [
        {
            "Metric": row.get("metric", ""),
            "Samples": row.get("samples", 0),
            "Rate (/s)": round(float(row.get("rate", 0)), 6),
            "Sum": round(float(row.get("sum", 0)), 6),
            "Average": round(float(row.get("average", 0)), 6),
        }
        for row in answer.get("metrics", [])
    ]


def _sample_rows(answer: dict) -> list:
    """The samples of the store, as the console shows them."""
    rows = []
    for sample in answer.get("samples", []):
        labels = sample.get("labels", {}) or {}
        rows.append({
            "Metric": sample.get("metric", ""),
            "Labels": ", ".join(f"{key}={value}" for key, value in sorted(labels.items())),
            "Value": sample.get("value", 0),
            "At": sample.get("at", ""),
        })
    return rows


def _rule_rows(answer: dict) -> list:
    """The alert rules, as the console shows them."""
    return [
        {
            "Rule": row.get("name", ""),
            "Metric": row.get("metric", ""),
            "Threshold": row.get("threshold", 0),
            "For (minutes)": round(float(row.get("for", 0)) / 60.0, 3),
            "Mode": row.get("mode", ""),
        }
        for row in answer.get("rules", [])
    ]


def _alert_rows(answer: dict) -> list:
    """The alerts of the last evaluation."""
    return [
        {
            "Rule": row.get("rule", ""),
            "Metric": row.get("metric", ""),
            "Value": row.get("value", 0),
            "Threshold": row.get("threshold", 0),
            "Since": row.get("since", ""),
        }
        for row in answer.get("alerts", [])
    ]


def _prometheus_rows(answer: dict) -> list:
    """The lines of the exporter, without the comments."""
    rows = []
    for line in answer.get("prometheus", "").split("\n"):
        if not line or line.startswith("#"):
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2:
            rows.append({"Metric": parts[0], "Value": parts[1]})
    return rows


def summary() -> dict:
    """The samples, the metrics and the alerts of the store."""
    answer = _answer()
    metrics = answer.get("metrics", [])
    rate_http = 0.0
    avg_queue = 0.0
    for row in metrics:
        if row.get("metric") == "http_requests_total":
            rate_http = float(row.get("rate", 0))
        if row.get("metric") == "queue_depth":
            avg_queue = float(row.get("average", 0))
    names = []
    for sample in answer.get("samples", []):
        if sample.get("metric") not in names:
            names.append(sample.get("metric"))
    return {
        "stats": {
            "samples": len(answer.get("samples", [])),
            "metrics": len(names),
            "rules": len(answer.get("rules", [])),
            "alerts": len(answer.get("alerts", [])),
            "rate_http": round(rate_http, 6),
            "avg_queue": round(avg_queue, 6),
        },
        "series": [
            {"label": row.get("metric", ""), "value": row.get("samples", 0)}
            for row in metrics
        ],
        "lists": {
            "metrics": _metric_rows(answer),
            "samples": _sample_rows(answer),
            "rules": _rule_rows(answer),
            "alerts": _alert_rows(answer),
            "prometheus": _prometheus_rows(answer),
        },
    }


# --------------------------------------------------------------------------
# the endpoints
# --------------------------------------------------------------------------

def _sample_from_form(body: dict, default_at: float) -> dict | None:
    """Build a sample from the fields of the form."""
    metric = str(body.get("metric") or "").strip()
    if not metric:
        return None
    try:
        value = float(str(body.get("value") or "0").strip() or "0")
    except ValueError:
        return None
    at = str(body.get("at") or "").strip()
    epoch = default_at
    if at:
        try:
            epoch = datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    labels = {}
    raw_labels = str(body.get("labels") or "").strip()
    for pair in raw_labels.split(","):
        if "=" in pair:
            key, label = pair.split("=", 1)
            labels[key.strip()] = label.strip()
    return {"metric": metric, "labels": labels, "value": value, "at": epoch}


def handle(method, path, payload, user):
    """The endpoints of the metrics store."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/metrics":
        return 200, {"metrics": _metric_rows(_answer())}
    if method == "GET" and path == "/api/samples":
        return 200, {"samples": _sample_rows(_answer())}
    if method == "GET" and path == "/api/rules":
        return 200, {"rules": _rule_rows(_answer())}
    if method == "GET" and path == "/api/alerts":
        return 200, {"alerts": _alert_rows(_answer())}
    if method == "GET" and path == "/api/prometheus":
        return 200, {"prometheus": _answer().get("prometheus", "")}
    if method == "POST" and path == "/api/ingest":
        body = payload or {}
        raw = body.get("samples")
        incoming = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                sample = _sample_from_form(item, _evaluation_now())
                if sample:
                    incoming.append(sample)
        elif isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        sample = _sample_from_form(item, _evaluation_now())
                        if sample:
                            incoming.append(sample)
        else:
            sample = _sample_from_form(body, _evaluation_now())
            if sample:
                incoming.append(sample)
        if not incoming:
            return 400, {"error": "no sample was sent"}
        _INGESTED.extend(incoming)
        answer = _answer(force=True)
        return 200, {
            "accepted": len(incoming),
            "samples": len(answer.get("samples", [])),
            "alerts": len(answer.get("alerts", [])),
        }
    if method == "POST" and path == "/api/query":
        body = payload or {}
        metric = str(body.get("metric") or "").strip()
        if not metric:
            return 400, {"error": "no metric was sent"}
        try:
            minutes = float(str(body.get("window_minutes") or "10").strip() or "10")
        except ValueError:
            return 400, {"error": "the window is not a number"}
        samples, _rules, now = _base_samples()
        samples = samples + list(_INGESTED)
        window = minutes * 60.0
        return 200, {
            "metric": metric,
            "window_minutes": minutes,
            "rate": round(_rate(samples, metric, window, now), 6),
            "sum": round(_sum(samples, metric, window, now), 6),
            "average": round(_average(samples, metric, window, now), 6),
        }
    return 404, {"error": "not found"}
