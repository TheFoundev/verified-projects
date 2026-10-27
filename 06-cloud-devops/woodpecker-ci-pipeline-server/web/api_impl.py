"""The API of the pipeline server: the builds, the steps and the logs.

The numbers come from the Go code of the project. A helper program is built
from the sources of the project (server.go, main.go and go.mod) with a `main`
that reads the pipeline file of the project with ParsePipeline, triggers a
build with Trigger and runs it with Run, so the steps really run on the
machine with the HostRunner of the project.

When the Go toolchain is not installed, the same code is mirrored in Python:
the same small YAML is parsed with the same rules and the commands of the
steps are run with the same shell, so the builds have the same status and the
same number of log lines.
"""

from __future__ import annotations

import hashlib
import json
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
TTL = 30.0

#: The pipeline file of the project, the one main.go parses.
SAMPLE_PIPELINE = """name: test
steps:
  - name: install
    image: golang:1.22
    commands:
      - go mod download
  - name: test
    image: golang:1.22
    commands:
      - go test ./...
"""

#: The repo and the commit of the demo of main.go.
DEFAULT_REPO = "foundev/web"
DEFAULT_COMMIT = "abc123"

#: The helper program: it parses the pipeline, triggers a build and runs it.
HELPER = r'''
package main

import (
	"encoding/json"
	"os"
	"time"
)

func main() {
	var input struct {
		Repo    string `json:"repo"`
		Commit  string `json:"commit"`
		Workdir string `json:"workdir"`
	}
	if err := json.NewDecoder(os.Stdin).Decode(&input); err != nil {
		os.Exit(1)
	}

	pipeline, err := ParsePipeline(samplePipeline)
	if err != nil {
		os.Exit(1)
	}

	server := NewServer()
	server.SetWorkdir(input.Workdir)
	build := server.Trigger(pipeline, input.Repo, input.Commit)
	finished, err := server.Run(build.ID)
	if err != nil {
		os.Exit(1)
	}

	steps := []map[string]any{}
	for _, step := range finished.Steps() {
		commands := []string{}
		commands = append(commands, step.Commands...)
		steps = append(steps, map[string]any{
			"name":     step.Name,
			"image":    step.Image,
			"commands": commands,
		})
	}

	logs := []string{}
	logs = append(logs, finished.Logs...)

	json.NewEncoder(os.Stdout).Encode(map[string]any{
		"pipeline": pipeline.Name,
		"steps":    steps,
		"build": map[string]any{
			"id":      finished.ID,
			"repo":    finished.Repo,
			"commit":  finished.Commit,
			"status":  finished.Status,
			"started": finished.Started.UTC().Format(time.RFC3339),
			"logs":    logs,
		},
	})
}
'''


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
    target = CACHE / f"woodpecker-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-woodpecker-"))
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


def _run_helper(repo: str, commit: str) -> dict | None:
    """Trigger a build and run it with the code of the project."""
    binary = _build_helper()
    if binary is None:
        return None
    payload = {"repo": repo, "commit": commit, "workdir": str(ROOT)}
    try:
        result = subprocess.run(
            [str(binary)], input=json.dumps(payload),
            capture_output=True, text=True, timeout=300,
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

def _trim_spaces(text: str) -> str:
    """trimSpaces of server.go."""
    start, end = 0, len(text)
    while start < end and text[start] in " \t\r":
        start += 1
    while end > start and text[end - 1] in " \t\r":
        end -= 1
    return text[start:end]


def _parse_pipeline(text: str) -> dict:
    """ParsePipeline of server.go, mirrored: the same small YAML subset."""
    pipeline = {"name": "", "steps": []}
    step = None
    for raw in text.split("\n"):
        trimmed = _trim_spaces(raw)
        if len(trimmed) > 5 and trimmed[:5] == "name:" and step is None:
            pipeline["name"] = _trim_spaces(trimmed[5:])
        elif len(trimmed) > 7 and trimmed[:7] == "- name:":
            step = {"name": _trim_spaces(trimmed[7:]), "image": "", "commands": []}
            pipeline["steps"].append(step)
        elif len(trimmed) > 6 and trimmed[:6] == "image:":
            if step is not None:
                step["image"] = _trim_spaces(trimmed[6:])
        elif len(trimmed) > 2 and trimmed[:2] == "- " and step is not None:
            step["commands"].append(_trim_spaces(trimmed[2:]))
    if not pipeline["name"] or not pipeline["steps"]:
        raise ValueError("pipeline needs a name and at least one step")
    return pipeline


def _shell(command: str, workdir: str) -> tuple:
    """HostRunner of server.go, mirrored: the exit code and the output."""
    try:
        process = subprocess.run(
            ["sh", "-c", command], cwd=workdir,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300,
        )
    except Exception as error:
        return 1, f"the command failed: {error}"
    return process.returncode, process.stdout.decode("utf-8", "replace")


def _fallback(repo: str, commit: str) -> dict:
    """The answer of the helper, computed in Python."""
    pipeline = _parse_pipeline(SAMPLE_PIPELINE)
    logs = []
    status = "success"
    for step in pipeline["steps"]:
        for command in step["commands"]:
            logs.append(f"[{step['name']}] $ {command}")
            exit_code, output = _shell(command, str(ROOT))
            if output:
                logs.append(output)
            if exit_code != 0:
                status = "failed"
                logs.append(f"the command exited with {exit_code}")
                break
        if status == "failed":
            break
    return {
        "pipeline": pipeline["name"],
        "steps": pipeline["steps"],
        "build": {
            "id": "build-1",
            "repo": repo,
            "commit": commit,
            "status": status,
            "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "logs": logs,
        },
    }


# --------------------------------------------------------------------------
# the state of the console
# --------------------------------------------------------------------------

_STATE: dict = {"builds": [], "at": 0.0}


def _trigger(repo: str, commit: str, force: bool = False) -> dict:
    """Trigger a build and run it. The answer of the helper is reused."""
    now = time.time()
    if not force and _STATE["builds"] and now - _STATE["at"] < TTL:
        return _STATE["builds"][-1]
    answer = _run_helper(repo, commit) or _fallback(repo, commit)
    build = answer.get("build") or {}
    build["steps"] = answer.get("steps") or []
    build["pipeline"] = answer.get("pipeline", "")
    # every build of the console gets its own number
    build["id"] = f"build-{len(_STATE['builds']) + 1}"
    _STATE["builds"].append(build)
    _STATE["at"] = now
    return build


def _builds() -> list:
    """Every build of the console."""
    if not _STATE["builds"]:
        _trigger(DEFAULT_REPO, DEFAULT_COMMIT)
    return _STATE["builds"]


# --------------------------------------------------------------------------
# the rows of the console
# --------------------------------------------------------------------------

def _build_rows(builds: list) -> list:
    """Every build with its status and the number of its log lines."""
    rows = []
    for build in builds:
        rows.append({
            "Build": build.get("id", "-"),
            "Repo": build.get("repo", "-"),
            "Commit": build.get("commit", "-"),
            "Status": build.get("status", "-"),
            "Log lines": len(build.get("logs") or []),
        })
    return rows


def _step_rows(builds: list) -> list:
    """The steps of the pipeline file, with the commands of every step."""
    rows = []
    steps = builds[-1].get("steps") or []
    for step in steps:
        rows.append({
            "Step": step.get("name", "-"),
            "Image": step.get("image", "-"),
            "Commands": ", ".join(step.get("commands") or []) or "-",
        })
    return rows


def _log_rows(builds: list) -> list:
    """Every line that the server recorded while it ran the steps."""
    rows = []
    for build in builds:
        for number, line in enumerate(build.get("logs") or [], start=1):
            rows.append({
                "Build": build.get("id", "-"),
                "Line": number,
                "Output": line.rstrip("\n"),
            })
    return rows


def summary() -> dict:
    """The builds of the server, the steps of the pipeline and the logs."""
    builds = _builds()
    logs = [line for build in builds for line in (build.get("logs") or [])]
    steps = builds[-1].get("steps") or []
    commands = sum(len(step.get("commands") or []) for step in steps)
    return {
        "stats": {
            "builds": len(builds),
            "steps": len(steps),
            "commands": commands,
            "succeeded": sum(1 for build in builds if build.get("status") == "success"),
            "failed": sum(1 for build in builds if build.get("status") == "failed"),
            "log_lines": len(logs),
        },
        "series": [
            {"label": build.get("id", "-"), "value": len(build.get("logs") or [])}
            for build in builds
        ],
        "lists": {
            "builds": _build_rows(builds),
            "steps": _step_rows(builds),
            "logs": _log_rows(builds),
        },
    }


# --------------------------------------------------------------------------
# the endpoints
# --------------------------------------------------------------------------

def handle(method, path, payload, user):
    """The endpoints of the pipeline server."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/builds":
        return 200, {"builds": summary()["lists"]["builds"]}
    if method == "GET" and path == "/api/steps":
        return 200, {"steps": summary()["lists"]["steps"]}
    if method == "GET" and path == "/api/logs":
        return 200, {"logs": summary()["lists"]["logs"]}
    if method == "POST" and path == "/api/trigger":
        body = payload or {}
        repo = str(body.get("repo") or DEFAULT_REPO).strip() or DEFAULT_REPO
        commit = str(body.get("commit") or DEFAULT_COMMIT).strip() or DEFAULT_COMMIT
        build = _trigger(repo, commit, force=True)
        return 200, {
            "build": build.get("id", "-"),
            "repo": build.get("repo", repo),
            "commit": build.get("commit", commit),
            "status": build.get("status", "-"),
            "log_lines": len(build.get("logs") or []),
        }
    return 404, {"error": "not found"}
