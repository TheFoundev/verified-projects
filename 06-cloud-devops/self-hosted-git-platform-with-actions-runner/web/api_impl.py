"""The API of the repository: the objects, the commits, the branches and the workflow.

The numbers come from the Go code of the project. The sample commit and the
sample workflow are read from main.go, the way they are written there, and a
helper program is built from the sources of the project (repository.go,
main.go and go.mod) with a `main` that replays the commits with
Repository.Commit, reads the workflow with ParseWorkflow and records the steps
with RunWorkflow. The helper prints the answer as JSON.

When the Go toolchain is not installed, the same code is mirrored here: the
objects are hashed the way git hashes them, the tree and the commit payload
are written as repository.go writes them and the workflow parser reads the
indentation of the file, so the hashes, the history and the jobs do not
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

#: The helper program: it replays the commits and reads the workflow.
HELPER = r'''
package main

import (
	"encoding/json"
	"os"
	"sort"
)

func main() {
	var input struct {
		Commits  []inCommit `json:"commits"`
		Workflow string     `json:"workflow"`
	}
	if err := json.NewDecoder(os.Stdin).Decode(&input); err != nil {
		os.Exit(1)
	}

	repository := NewRepository()
	commit := Commit{}
	for _, item := range input.Commits {
		commit = repository.Commit(item.Branch, item.Message, item.Files)
	}

	workflow, err := ParseWorkflow(input.Workflow)
	if err != nil {
		report(map[string]any{"error": err.Error()})
		return
	}

	objects := []Object{}
	for hash, object := range repository.objects {
		objects = append(objects, object)
	}
	sort.Slice(objects, func(i, j int) bool { return objects[i].Hash < objects[j].Hash })

	branches := map[string]string{}
	for name, hash := range repository.Branches() {
		branches[name] = hash
	}

	report(map[string]any{
		"commit":   commit,
		"history":  repository.Log(input.Commits[len(input.Commits)-1].Branch, 20),
		"branches": branches,
		"objects":  objects,
		"workflow": workflow,
		"results":  RunWorkflow(workflow),
	})
}

type inCommit struct {
	Branch  string            `json:"branch"`
	Message string            `json:"message"`
	Files   map[string]string `json:"files"`
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

def _go_string(text: str) -> str:
    """The escapes of a Go string."""
    out, index = [], 0
    while index < len(text):
        character = text[index]
        if character == "\\" and index + 1 < len(text):
            index += 1
            following = text[index]
            out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}.get(following, following))
        else:
            out.append(character)
        index += 1
    return "".join(out)


def _default_flag(name: str) -> str:
    """The default of a flag of main.go."""
    text = (ROOT / "main.go").read_text(encoding="utf-8")
    match = re.search(r'flag\.String\("' + name + r'",\s*"([^"]*)"', text)
    return match.group(1) if match else ""


def _sample_files() -> dict:
    """The files of the first commit of main.go."""
    text = (ROOT / "main.go").read_text(encoding="utf-8")
    match = re.search(r"files := map\[string\]string\{(.*?)\}\n", text, re.S)
    files = {}
    if match:
        for name, value in re.findall(r'"((?:[^"\\]|\\.)*)":\s*"((?:[^"\\]|\\.)*)"', match.group(1)):
            files[_go_string(name)] = _go_string(value)
    return files


def _sample_workflow() -> str:
    """The workflow of main.go, the sampleWorkflow constant."""
    text = (ROOT / "main.go").read_text(encoding="utf-8")
    start = text.index("const sampleWorkflow = `")
    start = text.index("`", start + len("const sampleWorkflow = ")) + 1
    end = text.index("`", start)
    return text[start:end]


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
    target = CACHE / f"gitplatform-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-gitplatform-"))
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


def _run_helper(commits: list, workflow: str) -> dict | None:
    """Ask the code of the project for the objects, the history and the jobs."""
    binary = _build_helper()
    if binary is None:
        return None
    payload = {"commits": commits, "workflow": workflow}
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

def _git_object_hash(kind: str, content: str) -> str:
    """gitObjectHash of repository.go, mirrored."""
    header = f"{kind} {len(content)}"
    return hashlib.sha1((header + "\x00" + content).encode()).hexdigest()


def _commit(branch: str, message: str, files: dict, parent: str = "",
            tree: str = "", author: str = "foundev", when: str = "") -> dict:
    """Repository.CommitAs of repository.go, mirrored."""
    objects = []
    tree_lines = ""
    for name in sorted(files):
        blob = _git_object_hash("blob", files[name])
        objects.append({"hash": blob, "type": "blob", "content": files[name]})
        tree_lines += f"{blob} {name}\n"
    tree_hash = _git_object_hash("tree", tree_lines)
    objects.append({"hash": tree_hash, "type": "tree", "content": tree_lines})
    payload = (
        f"branch:{branch}\nparent:{parent}\ntree:{tree_hash}\n"
        f"message:{message}\nauthor:{author}\nwhen:{when}\n"
    )
    commit_hash = _git_object_hash("commit", payload)
    objects.append({"hash": commit_hash, "type": "commit", "content": payload})
    return {
        "hash": commit_hash, "branch": branch, "message": message, "parent": parent,
        "tree": tree_hash, "files": files, "author": author, "when": when,
        "objects": objects,
    }


def _split_list(value: str) -> list:
    """splitList of repository.go, mirrored."""
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_workflow(text: str) -> dict:
    """ParseWorkflow of repository.go, mirrored."""
    workflow = {"name": "", "on": "", "jobs": []}
    job_index, step_index, in_env = -1, -1, False

    for line in text.split("\n"):
        line = line[:-1] if line.endswith("\r") else line
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = trimmed
        while content.startswith("-"):
            content = content[1:].strip()
        is_entry = content != trimmed

        if indent == 0 and content.startswith("name:"):
            workflow["name"] = content[len("name:"):].strip()
            continue
        if indent == 0 and content.startswith("on:"):
            workflow["on"] = content[len("on:"):].strip()
            continue
        if indent == 0 and content.startswith("jobs:"):
            job_index = -1
            continue
        if indent == 2 and is_entry and content.startswith("name:"):
            workflow["jobs"].append({"name": content[len("name:"):].strip(), "needs": [], "steps": []})
            job_index = len(workflow["jobs"]) - 1
            step_index = -1
            continue
        if indent == 2 and not is_entry and content.endswith(":"):
            name = content[:-1].strip()
            if not name:
                raise ValueError("a job needs a name")
            workflow["jobs"].append({"name": name, "needs": [], "steps": []})
            job_index = len(workflow["jobs"]) - 1
            step_index = -1
            continue
        if indent == 4 and content.startswith("needs:"):
            if job_index < 0:
                raise ValueError("a needs before any job")
            workflow["jobs"][job_index]["needs"] = _split_list(content[len("needs:"):].strip())
            continue
        if indent == 4 and content == "steps:":
            step_index = -1
            in_env = False
            continue
        if indent >= 6 and is_entry and content.startswith("name:"):
            if job_index < 0:
                raise ValueError("a step before any job")
            workflow["jobs"][job_index]["steps"].append(
                {"name": content[len("name:"):].strip(), "run": "", "uses": "",
                 "needs": [], "env": {}})
            step_index = len(workflow["jobs"][job_index]["steps"]) - 1
            in_env = False
            continue
        if indent >= 6 and is_entry and content.startswith("run:"):
            if job_index < 0:
                raise ValueError("a run before any job")
            workflow["jobs"][job_index]["steps"].append(
                {"name": "", "run": content[len("run:"):].strip(), "uses": "",
                 "needs": [], "env": {}})
            step_index = len(workflow["jobs"][job_index]["steps"]) - 1
            continue
        if indent >= 6 and is_entry and content.startswith("uses:"):
            if job_index < 0:
                raise ValueError("a uses before any job")
            workflow["jobs"][job_index]["steps"].append(
                {"name": "", "run": "", "uses": content[len("uses:"):].strip(),
                 "needs": [], "env": {}})
            step_index = len(workflow["jobs"][job_index]["steps"]) - 1
            continue
        if indent >= 6 and not is_entry and content.startswith("name:"):
            if job_index < 0:
                raise ValueError("a step before any job")
            workflow["jobs"][job_index]["steps"].append(
                {"name": content[len("name:"):].strip(), "run": "", "uses": "",
                 "needs": [], "env": {}})
            step_index = len(workflow["jobs"][job_index]["steps"]) - 1
            in_env = False
            continue
        if indent >= 6 and not is_entry and content.startswith("run:"):
            if job_index < 0 or step_index < 0:
                raise ValueError("a run before any step")
            workflow["jobs"][job_index]["steps"][step_index]["run"] = content[len("run:"):].strip()
            continue
        if indent >= 6 and not is_entry and content.startswith("uses:"):
            if job_index < 0 or step_index < 0:
                raise ValueError("a uses before any step")
            workflow["jobs"][job_index]["steps"][step_index]["uses"] = content[len("uses:"):].strip()
            continue
        if indent >= 6 and not is_entry and content.startswith("needs:"):
            if job_index < 0 or step_index < 0:
                raise ValueError("a needs before any step")
            workflow["jobs"][job_index]["steps"][step_index]["needs"] = _split_list(
                content[len("needs:"):].strip())
            continue
        if indent >= 6 and not is_entry and content.startswith("env:"):
            if job_index < 0 or step_index < 0:
                raise ValueError("an env before any step")
            workflow["jobs"][job_index]["steps"][step_index]["env"] = {}
            in_env = True
            continue
        if in_env and indent > 6:
            if job_index < 0 or step_index < 0:
                raise ValueError("an env value before any step")
            if "=" not in content:
                raise ValueError("an env value has to be written as KEY=value")
            key, value = content.split("=", 1)
            workflow["jobs"][job_index]["steps"][step_index]["env"][key] = value

    if not workflow["name"] or not workflow["jobs"]:
        raise ValueError("a workflow needs a name and a job")
    return workflow


def _run_workflow(workflow: dict) -> list:
    """RunWorkflow of repository.go, mirrored: nothing is run."""
    results = []
    for job in workflow.get("jobs", []):
        for step in job.get("steps", []):
            results.append({
                "job": job.get("name", ""), "step": step.get("name", ""),
                "status": "success", "log": step.get("run", ""),
            })
    return results


def _fallback(commits: list, workflow_text: str) -> dict:
    """The answer of the helper, computed in Python."""
    try:
        workflow = _parse_workflow(workflow_text)
    except ValueError as error:
        return {"error": str(error)}
    objects = []
    history = []
    branches = {}
    commit = {}
    for item in commits:
        branch = item.get("branch", "")
        commit = _commit(branch, item.get("message", ""), item.get("files", {}),
                         parent=branches.get(branch, ""))
        objects.extend(commit.pop("objects"))
        branches[commit["branch"]] = commit["hash"]
        history.append(commit)
    history.reverse()
    objects.sort(key=lambda obj: obj["hash"])
    return {
        "commit": commit,
        "history": history,
        "branches": branches,
        "objects": objects,
        "workflow": workflow,
        "results": _run_workflow(workflow),
    }


# --------------------------------------------------------------------------
# the state of the console
# --------------------------------------------------------------------------

#: The commits that were made through the API, after the one of main.go.
_COMMITS: list = []
_ANSWER: dict = {"at": 0.0, "key": None, "data": None}


def _sample_commits() -> list:
    """The commits of the repository: the one of main.go and the new ones."""
    first = {
        "branch": _default_flag("branch") or "main",
        "message": _default_flag("message") or "initial commit",
        "files": _sample_files(),
    }
    return [first] + list(_COMMITS)


def _answer(force: bool = False) -> dict:
    """The objects, the history and the workflow, computed by the code."""
    commits = _sample_commits()
    workflow = _sample_workflow()
    key = json.dumps([commits, workflow], sort_keys=True)
    moment = time.time()
    if (not force and _ANSWER["data"] is not None and _ANSWER["key"] == key
            and moment - _ANSWER["at"] < TTL):
        return _ANSWER["data"]
    answer = _run_helper(commits, workflow) or _fallback(commits, workflow)
    _ANSWER.update({"at": moment, "key": key, "data": answer})
    return answer


# --------------------------------------------------------------------------
# the rows of the console
# --------------------------------------------------------------------------

def _commit_rows(answer: dict) -> list:
    """The commits of the repository, newest first."""
    return [
        {
            "Commit": commit.get("hash", ""),
            "Branch": commit.get("branch", ""),
            "Message": commit.get("message", ""),
            "Parent": commit.get("parent", ""),
            "Tree": commit.get("tree", ""),
            "Author": commit.get("author", ""),
        }
        for commit in answer.get("history", [])
    ]


def _branch_rows(answer: dict) -> list:
    """The branches and the commit they point at."""
    return [
        {"Branch": name, "Commit": answer.get("branches", {}).get(name, "")}
        for name in sorted(answer.get("branches", {}))
    ]


def _object_rows(answer: dict) -> list:
    """The objects of the repository."""
    rows = []
    for obj in answer.get("objects", []):
        content = obj.get("content", "")
        rows.append({
            "Object": obj.get("hash", ""),
            "Type": obj.get("type", ""),
            "Size": len(content),
            "Content": content if len(content) <= 60 else content[:57] + "...",
        })
    return rows


def _job_rows(answer: dict) -> list:
    """The jobs of the workflow."""
    return [
        {
            "Job": job.get("name", ""),
            "Needs": ", ".join(job.get("needs", [])),
            "Steps": len(job.get("steps", [])),
        }
        for job in answer.get("workflow", {}).get("jobs", [])
    ]


def _step_rows(answer: dict) -> list:
    """The steps of the jobs of the workflow."""
    rows = []
    for job in answer.get("workflow", {}).get("jobs", []):
        for step in job.get("steps", []):
            rows.append({
                "Job": job.get("name", ""),
                "Step": step.get("name", ""),
                "Run": step.get("run", ""),
                "Uses": step.get("uses", ""),
                "Needs": ", ".join(step.get("needs", [])),
            })
    return rows


def _result_rows(answer: dict) -> list:
    """The results of the dry run of the workflow."""
    return [
        {
            "Job": result.get("job", ""),
            "Step": result.get("step", ""),
            "Status": result.get("status", ""),
            "Log": result.get("log", ""),
            "Reason": result.get("reason", ""),
        }
        for result in answer.get("results", [])
    ]


def summary() -> dict:
    """The objects, the commits, the branches and the workflow."""
    answer = _answer()
    workflow = answer.get("workflow", {}) or {}
    jobs = workflow.get("jobs", [])
    return {
        "stats": {
            "commits": len(answer.get("history", [])),
            "objects": len(answer.get("objects", [])),
            "branches": len(answer.get("branches", {})),
            "jobs": len(jobs),
            "steps": sum(len(job.get("steps", [])) for job in jobs),
            "results": len(answer.get("results", [])),
        },
        "series": [
            {"label": job.get("name", ""), "value": len(job.get("steps", []))}
            for job in jobs
        ],
        "lists": {
            "commits": _commit_rows(answer),
            "branches": _branch_rows(answer),
            "objects": _object_rows(answer),
            "jobs": _job_rows(answer),
            "steps": _step_rows(answer),
            "results": _result_rows(answer),
        },
    }


# --------------------------------------------------------------------------
# the endpoints
# --------------------------------------------------------------------------

def _files_from_form(body: dict) -> dict:
    """The files of a commit, read from the fields of the form."""
    files = {}
    raw = body.get("files")
    if isinstance(raw, dict):
        for name, content in raw.items():
            files[str(name)] = str(content)
        return files
    for line in str(raw or "").split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        name, content = line.split("=", 1)
        files[name.strip()] = content
    return files


def handle(method, path, payload, user):
    """The endpoints of the repository."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/commits":
        return 200, {"commits": _commit_rows(_answer())}
    if method == "GET" and path == "/api/branches":
        return 200, {"branches": _branch_rows(_answer())}
    if method == "GET" and path == "/api/objects":
        return 200, {"objects": _object_rows(_answer())}
    if method == "GET" and path == "/api/jobs":
        return 200, {"jobs": _job_rows(_answer())}
    if method == "GET" and path == "/api/steps":
        return 200, {"steps": _step_rows(_answer())}
    if method == "GET" and path == "/api/results":
        return 200, {"results": _result_rows(_answer())}
    if method == "POST" and path == "/api/commit":
        body = payload or {}
        branch = str(body.get("branch") or _default_flag("branch") or "main").strip() or "main"
        message = str(body.get("message") or "").strip()
        files = _files_from_form(body)
        if not message:
            return 400, {"error": "a commit needs a message"}
        if not files:
            return 400, {"error": "a commit needs at least one file"}
        _COMMITS.append({"branch": branch, "message": message, "files": files})
        answer = _answer(force=True)
        commit = answer.get("commit", {})
        return 200, {
            "commit": commit.get("hash", ""),
            "branch": branch,
            "parent": commit.get("parent", ""),
            "tree": commit.get("tree", ""),
            "commits": len(answer.get("history", [])),
            "objects": len(answer.get("objects", [])),
        }
    if method == "POST" and path == "/api/workflow":
        body = payload or {}
        text = body.get("workflow")
        if not isinstance(text, str) or not text.strip():
            text = _sample_workflow()
        try:
            workflow = _parse_workflow(text)
        except ValueError as error:
            return 400, {"error": str(error)}
        return 200, {
            "name": workflow["name"],
            "on": workflow["on"],
            "jobs": _job_rows({"workflow": workflow}),
            "steps": _step_rows({"workflow": workflow}),
        }
    return 404, {"error": "not found"}
