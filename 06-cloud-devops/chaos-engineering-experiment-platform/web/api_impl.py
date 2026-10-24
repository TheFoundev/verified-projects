"""The API of the chaos platform: the timeline of the experiments.

The numbers come from the Go code of the project. A helper program is built
from the sources of the project (chaos.go, main.go and go.mod) with a `main`
that builds an Experiment and calls Runner.Run with the DryRunExecutor, so
nothing on the machine is harmed, and prints the Result as JSON. When the Go
toolchain is not installed, the same runner is mirrored here: the dry run
changes nothing and the clock moves with the durations of the actions, so the
run does not wait either.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(tempfile.gettempdir()) / "foundev-go-helpers"
GO = shutil.which("go")

#: The kinds of fault Validate accepts.
KINDS = ("delay", "kill", "stress")

#: The helper program: it runs an experiment and prints the result as JSON.
HELPER = r'''
package main

import (
	"context"
	"encoding/json"
	"flag"
	"os"
	"time"
)

func main() {
	target := flag.String("target", "checkout", "the target service")
	kind := flag.String("fault", "delay", "delay, kill or stress")
	seconds := flag.Float64("seconds", 2, "the duration of the action in seconds")
	magnitude := flag.Int("magnitude", 250, "the magnitude of the action")
	metric := flag.String("metric", "", "the metric of the hypothesis")
	op := flag.String("op", "lt", "lt, gt or eq")
	reading := flag.Float64("reading", 0.001, "the value of the metric")
	threshold := flag.Float64("threshold", 0.01, "the threshold of the hypothesis")
	flag.Parse()

	experiment := Experiment{
		Name:      "student-chaos",
		Namespace: "default",
		Actions: []Action{{
			Kind:      *kind,
			Target:    *target,
			Duration:  time.Duration(*seconds * float64(time.Second)),
			Magnitude: *magnitude,
		}},
	}
	if *metric != "" {
		experiment.Hypothesis = &Hypothesis{
			Service: *target, Metric: *metric, Op: *op, Value: *threshold,
		}
	}

	// The clock moves with the durations of the actions, so the dry run does
	// not wait but the timeline still shows the length of the faults.
	current := time.Now()
	runner := Runner{
		Executor: DryRunExecutor{},
		Now:      func() time.Time { return current },
		Sleep:    func(d time.Duration) { current = current.Add(d) },
	}
	if experiment.Hypothesis != nil {
		runner.Metrics = StaticMetricReader{
			Values: map[string]float64{*target + "/" + *metric: *reading},
		}
	}

	result, err := runner.Run(context.Background(), experiment)
	if err != nil {
		json.NewEncoder(os.Stdout).Encode(map[string]any{"error": err.Error()})
		os.Exit(1)
	}

	json.NewEncoder(os.Stdout).Encode(map[string]any{
		"experiment": experiment.Name,
		"namespace":  experiment.Namespace,
		"actions":    experiment.Actions,
		"affected":   AffectedServices(experiment),
		"result":     result,
	})
}
'''

#: The last run and the runs the console has made.
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
    target = CACHE / f"chaos-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-chaos-"))
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


def _run_helper(arguments: list) -> dict | None:
    """Run the helper and read its JSON answer."""
    binary = _build_helper()
    if binary is None:
        return None
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


def _validate(target: str, kind: str, seconds: float, metric: str, op: str) -> None:
    """The checks of Validate in chaos.go."""
    if kind not in KINDS:
        raise ValueError(f'unsupported action "{kind}"')
    if not target:
        raise ValueError(f'action "{kind}" needs a target')
    if seconds < 0:
        raise ValueError(f'action "{kind}" has a negative duration')
    if metric:
        if not target:
            raise ValueError("hypothesis needs a service")
        if op not in ("lt", "gt", "eq"):
            raise ValueError(f'unsupported hypothesis operator "{op}"')


def _compare(op: str, value: float, threshold: float) -> bool:
    """The operators of Compare in chaos.go."""
    if op == "lt":
        return value < threshold
    if op == "gt":
        return value > threshold
    if op == "eq":
        return value == threshold
    return False


def _duration_string(delta: timedelta) -> str:
    """A duration the way the Go Duration.String writes it."""
    seconds = delta.total_seconds()
    if seconds < 60:
        if seconds == int(seconds):
            return f"{int(seconds)}s"
        return f"{seconds:g}s"
    minutes, rest = divmod(seconds, 60)
    if rest == 0:
        return f"{int(minutes)}m"
    return f"{int(minutes)}m{rest:g}s"


def _note(kind: str, magnitude: int) -> str:
    """The note of Runner.note in chaos.go."""
    if kind == "delay":
        return f"add {magnitude}ms latency"
    if kind == "kill":
        return "terminate the process once and let it restart"
    if kind == "stress":
        return f"consume {magnitude} cpu shares"
    return ""


def _run_in_python(target: str, kind: str, seconds: float, magnitude: int,
                   metric: str, op: str, reading: float, threshold: float) -> dict:
    """Runner.Run of chaos.go, mirrored.

    The dry-run executor changes nothing and the clock moves with the
    duration of the action, so the run does not wait. The steady-state check
    before the first action and after every recovery is the same one.
    """
    started = datetime.now(timezone.utc)
    current = started
    events = []
    steady = []
    affected = []
    applied = []
    aborted = False
    abort_reason = ""
    actions_run = 0

    def stamp(moment: datetime) -> str:
        return moment.isoformat()

    def check(phase: str):
        if not metric:
            return None, True
        value = reading
        ok = _compare(op, value, threshold)
        entry = {
            "at": stamp(current),
            "phase": phase,
            "metric": metric,
            "value": value,
            "ok": ok,
            "note": "",
        }
        if not ok:
            entry["note"] = f"steady state broken: {metric} {value} is not {op} {threshold}"
        return entry, ok

    def rollback():
        for action in reversed(applied):
            events.append({
                "action": action["kind"],
                "target": action["target"],
                "phase": "rolled-back",
                "at": stamp(current),
                "note": "rolled back after the steady state broke",
            })

    entry, ok = check("before")
    if metric:
        steady.append(entry)
    if not ok:
        aborted = True
        abort_reason = "the steady state was already broken: " + entry["note"]
    else:
        action = {
            "kind": kind,
            "target": target,
            "duration": int(seconds * 1_000_000_000),
            "magnitude": magnitude,
        }
        applied.append(action)
        events.append({
            "action": kind,
            "target": target,
            "phase": "applied",
            "at": stamp(current),
            "note": _note(kind, magnitude),
        })
        if seconds > 0:
            current = current + timedelta(seconds=seconds)
        events.append({
            "action": kind,
            "target": target,
            "phase": "recovered",
            "at": stamp(current),
            "note": "fault removed",
        })
        applied.pop()
        if target not in affected:
            affected.append(target)
        actions_run += 1
        entry, ok = check("after")
        if metric:
            steady.append(entry)
        if not ok:
            rollback()
            aborted = True
            abort_reason = "the steady state broke: " + entry["note"]

    finished = current
    result = {
        "experiment": "student-chaos",
        "events": events,
        "affected": affected,
        "duration": _duration_string(finished - started),
        "started_at": stamp(started),
        "finished_at": stamp(finished),
        "aborted": aborted,
        "steady_state": steady,
        "actions_run": actions_run,
        "actions_total": 1,
    }
    if abort_reason:
        result["abort_reason"] = abort_reason
    return {
        "experiment": "student-chaos",
        "namespace": "default",
        "actions": [{
            "kind": kind,
            "target": target,
            "duration": int(seconds * 1_000_000_000),
            "magnitude": magnitude,
        }],
        "affected": sorted({target}),
        "result": result,
    }


def _run(target: str, kind: str, seconds: float, magnitude: int,
         metric: str, op: str, reading: float, threshold: float) -> dict:
    """Run an experiment and answer with the result of the runner."""
    _validate(target, kind, seconds, metric, op)
    arguments = [
        "-target", target,
        "-fault", kind,
        "-seconds", str(seconds),
        "-magnitude", str(magnitude),
    ]
    if metric:
        arguments += [
            "-metric", metric,
            "-op", op,
            "-reading", str(reading),
            "-threshold", str(threshold),
        ]
    answer = _run_helper(arguments)
    if answer:
        return answer
    return _run_in_python(target, kind, seconds, magnitude, metric, op, reading, threshold)


def _action_rows(actions: list) -> list:
    """The actions of an experiment, with the duration in seconds."""
    rows = []
    for action in actions:
        nanoseconds = action.get("duration", 0)
        rows.append({
            "Kind": action.get("kind", ""),
            "Target": action.get("target", ""),
            "Duration": f"{nanoseconds / 1_000_000_000:g}s",
            "Magnitude": action.get("magnitude", 0),
        })
    return rows


def _last() -> dict:
    """The result of the last experiment."""
    if "last" not in _STATE:
        _STATE["last"] = _run("checkout", "delay", 2.0, 250, "error_rate", "lt", 0.001, 0.01)
    return _STATE["last"]


def summary() -> dict:
    """The counters of the last experiment and its timeline."""
    last = _last()
    result = last.get("result", {})
    events = result.get("events", [])
    steady = result.get("steady_state", [])
    affected = result.get("affected", last.get("affected", []))
    return {
        "stats": {
            "actions_run": result.get("actions_run", 0),
            "actions_total": result.get("actions_total", 0),
            "events": len(events),
            "affected": len(affected),
            "duration": result.get("duration", ""),
            "aborted": "yes" if result.get("aborted") else "no",
        },
        "series": [
            {"label": event["phase"], "value": 1} for event in events
        ],
        "lists": {
            "events": [
                {
                    "Phase": event.get("phase", ""),
                    "Action": event.get("action", ""),
                    "Target": event.get("target", ""),
                    "At": event.get("at", ""),
                    "Note": event.get("note", ""),
                }
                for event in events
            ],
            "actions": _action_rows(last.get("actions", [])),
            "steady_state": [
                {
                    "Phase": check.get("phase", ""),
                    "Metric": check.get("metric", ""),
                    "Value": check.get("value", 0),
                    "OK": "yes" if check.get("ok") else "no",
                    "Note": check.get("note", ""),
                }
                for check in steady
            ],
        },
    }


def _experiments() -> list:
    """The experiments that were run through this console."""
    return _STATE.setdefault("experiments", [])


def handle(method, path, payload, user):
    """The read and the write endpoints of the platform."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/experiments":
        return 200, {"experiments": _experiments()}
    if method == "GET" and path == "/api/events":
        return 200, {"events": summary()["lists"]["events"]}
    if method == "POST" and path == "/api/experiment":
        body = payload or {}
        target = str(body.get("service") or body.get("target") or "").strip()
        kind = str(body.get("action") or body.get("fault") or "delay").strip()
        try:
            seconds = float(body.get("duration") or body.get("seconds") or 2)
        except (TypeError, ValueError):
            return 400, {"error": "the duration has to be a number of seconds"}
        try:
            magnitude = int(float(body.get("magnitude") or 250))
        except (TypeError, ValueError):
            return 400, {"error": "the magnitude has to be a number"}
        metric = str(body.get("metric") or "").strip()
        op = str(body.get("op") or "lt").strip()
        try:
            reading = float(body.get("value") or body.get("reading") or 0.001)
        except (TypeError, ValueError):
            return 400, {"error": "the reading of the metric has to be a number"}
        try:
            threshold = float(body.get("threshold") or 0.01)
        except (TypeError, ValueError):
            return 400, {"error": "the threshold of the hypothesis has to be a number"}
        try:
            answer = _run(target, kind, seconds, magnitude, metric, op, reading, threshold)
        except ValueError as error:
            return 400, {"error": str(error)}
        result = answer.get("result", {})
        _STATE["last"] = answer
        _experiments().insert(0, {
            "Experiment": answer.get("experiment", ""),
            "Namespace": answer.get("namespace", ""),
            "Service": target,
            "Action": kind,
            "Duration": result.get("duration", ""),
            "Events": len(result.get("events", [])),
            "Aborted": "yes" if result.get("aborted") else "no",
        })
        return 200, {
            "experiment": answer.get("experiment", ""),
            "actions_run": result.get("actions_run", 0),
            "actions_total": result.get("actions_total", 0),
            "events": len(result.get("events", [])),
            "affected": len(result.get("affected", [])),
            "duration": result.get("duration", ""),
            "aborted": "yes" if result.get("aborted") else "no",
        }
    return 404, {"error": "not found"}
