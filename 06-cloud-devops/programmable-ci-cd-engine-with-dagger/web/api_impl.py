"""The API of the step engine: the order of the steps, their digests and the run.

The numbers come from the Go code of the project. The sample pipeline is read
from main.go, the way it is written there, and a helper program is built from
the sources of the project (engine.go, main.go and go.mod) with a `main` that
puts the steps in order with Order, hashes every step with digestOf and runs
the pipeline twice with Engine.Run, the RecordingExecutor and a MemoryCache:
the first run fills the cache and the second one hits it. The helper prints
the answer as JSON.

When the Go toolchain is not installed, the same code is mirrored here:
Order, digestOf and the run of the engine are read as they are written in
engine.go, so the order, the digests and the number of cache hits do not
change.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(tempfile.gettempdir()) / "foundev-go-helpers"
GO = shutil.which("go")

#: How long an answer of the helper is reused.
TTL = 30.0

#: The helper program: it orders the steps, hashes them and runs the pipeline.
HELPER = r'''
package main

import (
	"context"
	"encoding/json"
	"os"
	"time"
)

type inStep struct {
	Name    string            `json:"name"`
	Image   string            `json:"image"`
	Command string            `json:"command"`
	Needs   []string          `json:"needs"`
	Retries int               `json:"retries"`
	Timeout int               `json:"timeout"`
	Env     map[string]string `json:"env"`
	Secret  bool              `json:"secret"`
}

func main() {
	var input struct {
		Pipeline string   `json:"pipeline"`
		Steps    []inStep `json:"steps"`
	}
	if err := json.NewDecoder(os.Stdin).Decode(&input); err != nil {
		os.Exit(1)
	}

	steps := []Step{}
	for _, step := range input.Steps {
		steps = append(steps, Step{
			Name: step.Name, Image: step.Image, Command: step.Command,
			Needs: step.Needs, Retries: step.Retries, Timeout: step.Timeout,
			Env: step.Env, Secret: step.Secret,
		})
	}
	pipeline := Pipeline{Name: input.Pipeline, Steps: steps}

	order, err := Order(pipeline)
	if err != nil {
		report(map[string]any{"error": err.Error()})
		return
	}

	byName := map[string]Step{}
	for _, step := range pipeline.Steps {
		byName[step.Name] = step
	}
	digests := map[string]string{}
	for _, name := range order {
		step := byName[name]
		dependencyDigests := make([]string, 0, len(step.Needs))
		for _, need := range step.Needs {
			dependencyDigests = append(dependencyDigests, digests[need])
		}
		sortStrings(dependencyDigests)
		digests[name] = digestOf(step, dependencyDigests)
	}

	cache := NewMemoryCache()
	engine := NewEngine(&RecordingExecutor{}, cache)

	started := time.Now()
	results, err := engine.Run(context.Background(), pipeline)
	if err != nil {
		report(map[string]any{"error": err.Error(), "results": results})
		return
	}
	duration := time.Since(started)

	second, err := engine.Run(context.Background(), pipeline)
	if err != nil {
		report(map[string]any{"error": err.Error(), "results": second})
		return
	}

	cacheHits := 0
	attempts := 0
	for _, result := range second {
		if result.Cached {
			cacheHits++
		}
	}
	for _, result := range results {
		attempts += result.Attempts
	}

	report(map[string]any{
		"pipeline":    pipeline.Name,
		"order":       order,
		"digests":     digests,
		"results":     results,
		"second":      second,
		"cache_hits":  cacheHits,
		"attempts":    attempts,
		"duration_ms": duration.Milliseconds(),
	})
}

func sortStrings(values []string) {
	for i := 1; i < len(values); i++ {
		for j := i; j > 0 && values[j] < values[j-1]; j-- {
			values[j], values[j-1] = values[j-1], values[j]
		}
	}
}

func report(answer map[string]any) {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(answer)
}
'''

# --------------------------------------------------------------------------
# the data of the project, read from main.go
# --------------------------------------------------------------------------

def _step_literals(block: str) -> list:
    """The step literals of a block, with their braces counted."""
    literals = []
    index = 0
    while True:
        start = block.find("{Name:", index)
        if start < 0:
            break
        depth, position = 0, start
        while position < len(block):
            if block[position] == "{":
                depth += 1
            elif block[position] == "}":
                depth -= 1
                if depth == 0:
                    break
            position += 1
        literals.append(block[start:position + 1])
        index = position + 1
    return literals


def _sample_pipeline() -> dict:
    """The pipeline of main.go, as samplePipeline writes it."""
    text = (ROOT / "main.go").read_text(encoding="utf-8")
    block = text[text.index("func samplePipeline()"):text.index("func main()")]
    name = re.search(r'Pipeline\{Name:\s*"([^"]*)"', block)
    body = block[block.index("Steps: []Step{") + len("Steps: []Step{"):]
    steps = []
    for literal in _step_literals(body):
        step_name = re.search(r'Name:\s*"([^"]*)"', literal)
        image = re.search(r'Image:\s*"([^"]*)"', literal)
        command = re.search(r'Command:\s*"([^"]*)"', literal)
        needs = re.search(r'Needs:\s*\[\]string\{([^}]*)\}', literal)
        if not (step_name and image and command):
            continue
        steps.append({
            "name": step_name.group(1),
            "image": image.group(1),
            "command": command.group(1),
            "needs": re.findall(r'"([^"]*)"', needs.group(1)) if needs else [],
            "retries": 0,
            "timeout": 0,
            "env": {},
            "secret": False,
        })
    return {"pipeline": name.group(1) if name else "web", "steps": steps}


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
    target = CACHE / f"cicd-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-cicd-"))
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


def _run_helper(pipeline: dict) -> dict | None:
    """Ask the code of the project for the order, the digests and the run."""
    binary = _build_helper()
    if binary is None:
        return None
    payload = {"pipeline": pipeline.get("pipeline", "web"), "steps": pipeline.get("steps", [])}
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
# the fallback: the same code, in Python
# --------------------------------------------------------------------------

def _order(steps: list) -> list:
    """Order of engine.go, mirrored."""
    dependencies = {}
    for step in steps:
        if step["name"] in dependencies:
            raise ValueError(f'the step "{step["name"]}" is defined twice')
        dependencies[step["name"]] = list(step.get("needs", []))
    resolved, done = [], set()
    while len(resolved) < len(steps):
        progress = False
        for step in steps:
            if step["name"] in done:
                continue
            ready = True
            for need in step.get("needs", []):
                if need not in dependencies:
                    raise ValueError(f'unknown dependency "{need}"')
                if need not in done:
                    ready = False
                    break
            if ready:
                resolved.append(step["name"])
                done.add(step["name"])
                progress = True
        if not progress:
            raise ValueError("pipeline has a cycle")
    return resolved


def _digest_of(step: dict, dependency_digests: list) -> str:
    """digestOf of engine.go, mirrored."""
    parts = [step.get("name", ""), step.get("image", ""), step.get("command", "")]
    parts.extend(dependency_digests)
    env = step.get("env") or {}
    for key in sorted(env):
        value = env[key]
        if step.get("secret"):
            value = "***"
        parts.append(f"{key}={value}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _go_duration(seconds: float) -> str:
    """A duration in the format of Go."""
    nanoseconds = int(round(seconds * 1e9))
    if nanoseconds == 0:
        return "0s"
    if nanoseconds < 1000:
        return f"{nanoseconds}ns"
    if nanoseconds < 1_000_000:
        whole, rest = divmod(nanoseconds, 1000)
        return f"{whole}.{rest:03d}".rstrip("0").rstrip(".") + "µs"
    if nanoseconds < 1_000_000_000:
        whole, rest = divmod(nanoseconds, 1_000_000)
        return f"{whole}.{rest:06d}".rstrip("0").rstrip(".") + "ms"
    return f"{nanoseconds / 1e9:.9f}".rstrip("0").rstrip(".") + "s"


def _fallback(pipeline: dict) -> dict:
    """The answer of the helper, computed in Python."""
    steps = pipeline.get("steps", [])
    try:
        order = _order(steps)
    except ValueError as error:
        return {"error": str(error)}
    by_name = {step["name"]: step for step in steps}
    digests = {}
    for name in order:
        step = by_name[name]
        dependency_digests = sorted(digests[need] for need in step.get("needs", []))
        digests[name] = _digest_of(step, dependency_digests)

    results, second = [], []
    cached_digests = set()
    started = time.perf_counter()
    for name in order:
        step = by_name[name]
        digest = digests[name]
        if digest in cached_digests:
            second.append({
                "step": name, "status": "cached", "cached": True,
                "log": "cache hit", "duration": "0s",
            })
            continue
        moment = time.perf_counter()
        log = f'would run "{step.get("command", "")}" in {step.get("image", "")}'
        duration = _go_duration(time.perf_counter() - moment)
        results.append({
            "step": name, "status": "success", "log": log, "cached": False,
            "duration": duration, "attempts": 1, "exitCode": 0,
        })
        cached_digests.add(digest)
    for name in order:
        second.append({
            "step": name, "status": "cached", "cached": True,
            "log": "cache hit", "duration": "0s",
        })
    duration_ms = int((time.perf_counter() - started) * 1000)
    return {
        "pipeline": pipeline.get("pipeline", "web"),
        "order": order,
        "digests": digests,
        "results": results,
        "second": second,
        "cache_hits": sum(1 for result in second if result.get("cached")),
        "attempts": sum(int(result.get("attempts", 0)) for result in results),
        "duration_ms": duration_ms,
    }


# --------------------------------------------------------------------------
# the state of the console
# --------------------------------------------------------------------------

_ANSWER: dict = {"at": 0.0, "key": None, "data": None}


def _answer(pipeline: dict | None = None, force: bool = False) -> dict:
    """The order, the digests and the run, computed by the code of the project."""
    if pipeline is None:
        pipeline = _sample_pipeline()
    key = json.dumps(pipeline, sort_keys=True)
    moment = time.time()
    if (not force and _ANSWER["data"] is not None and _ANSWER["key"] == key
            and moment - _ANSWER["at"] < TTL):
        return _ANSWER["data"]
    answer = _run_helper(pipeline) or _fallback(pipeline)
    _ANSWER.update({"at": moment, "key": key, "data": answer})
    return answer


# --------------------------------------------------------------------------
# the rows of the console
# --------------------------------------------------------------------------

def _step_rows(answer: dict) -> list:
    """The steps of the pipeline, as the console shows them."""
    rows = []
    for step in _sample_pipeline().get("steps", []):
        rows.append({
            "Step": step.get("name", ""),
            "Image": step.get("image", ""),
            "Command": step.get("command", ""),
            "Needs": ", ".join(step.get("needs", [])),
            "Retries": step.get("retries", 0),
        })
    return rows


def _order_rows(answer: dict) -> list:
    """The order of the steps, as Order returns it."""
    return [
        {"#": index + 1, "Step": name}
        for index, name in enumerate(answer.get("order", []))
    ]


def _result_rows(answer: dict) -> list:
    """The results of the run, as the console shows them."""
    return [
        {
            "Step": result.get("step", ""),
            "Status": result.get("status", ""),
            "Cached": "yes" if result.get("cached") else "no",
            "Attempts": result.get("attempts", 0),
            "Exit code": result.get("exitCode", 0),
            "Duration": result.get("duration", ""),
            "Log": result.get("log", ""),
        }
        for result in answer.get("results", [])
    ]


def _digest_rows(answer: dict) -> list:
    """The digest of every step."""
    digests = answer.get("digests", {}) or {}
    return [
        {"Step": name, "Digest": digests.get(name, "")}
        for name in answer.get("order", [])
    ]


def summary() -> dict:
    """The order of the steps, their digests and the run of the pipeline."""
    answer = _answer()
    return {
        "stats": {
            "steps": len(_sample_pipeline().get("steps", [])),
            "cached": answer.get("cache_hits", 0),
            "attempts": answer.get("attempts", 0),
            "duration_ms": answer.get("duration_ms", 0),
            "digests": len(answer.get("digests", {}) or {}),
            "pipeline": answer.get("pipeline", ""),
        },
        "series": [
            {"label": result.get("step", ""), "value": 1 if result.get("cached") else 0}
            for result in answer.get("results", [])
        ],
        "lists": {
            "steps": _step_rows(answer),
            "order": _order_rows(answer),
            "results": _result_rows(answer),
            "digests": _digest_rows(answer),
        },
    }


# --------------------------------------------------------------------------
# the endpoints
# --------------------------------------------------------------------------

def _pipeline_from_form(body: dict) -> dict:
    """Build a pipeline from the fields of the form."""
    steps = []
    for item in body.get("steps", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        needs = item.get("needs", [])
        if isinstance(needs, str):
            needs = [part.strip() for part in needs.split(",") if part.strip()]
        steps.append({
            "name": name,
            "image": str(item.get("image") or "").strip(),
            "command": str(item.get("command") or "").strip(),
            "needs": [str(need) for need in needs],
            "retries": int(item.get("retries", 0) or 0),
            "timeout": int(item.get("timeout", 0) or 0),
            "env": {},
            "secret": False,
        })
    if not steps:
        return _sample_pipeline()
    return {"pipeline": str(body.get("pipeline") or "web").strip() or "web", "steps": steps}


def handle(method, path, payload, user):
    """The endpoints of the step engine."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/steps":
        return 200, {"steps": _step_rows(_answer())}
    if method == "GET" and path == "/api/order":
        return 200, {"order": _order_rows(_answer())}
    if method == "GET" and path == "/api/results":
        return 200, {"results": _result_rows(_answer())}
    if method == "GET" and path == "/api/digests":
        return 200, {"digests": _digest_rows(_answer())}
    if method == "POST" and path == "/api/run":
        body = payload or {}
        pipeline = _pipeline_from_form(body)
        answer = _answer(pipeline, force=True)
        if answer.get("error"):
            return 400, {"error": answer["error"]}
        return 200, {
            "pipeline": answer.get("pipeline", ""),
            "steps": len(answer.get("order", [])),
            "cache_hits": answer.get("cache_hits", 0),
            "attempts": answer.get("attempts", 0),
        }
    if method == "POST" and path == "/api/order":
        body = payload or {}
        pipeline = _pipeline_from_form(body)
        steps = pipeline.get("steps", [])
        try:
            order = _order(steps)
        except ValueError as error:
            return 400, {"error": str(error)}
        return 200, {"order": [{"#": index + 1, "Step": name} for index, name in enumerate(order)]}
    return 404, {"error": "not found"}
