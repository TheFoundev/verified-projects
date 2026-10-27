"""The API of the control plane: the resources, the plan and the reconciliation.

The numbers come from the Go code of the project. A helper program is built
from the sources of the project (control.go, main.go and go.mod) with a
`main` that fills a Store, registers the two schemas of main.go, sets the
desired state with Controller.SetDesired, plans the actions with Plan and
applies them with Controller.Reconcile, and prints the result as JSON. The
console shows the resources of the first reconciliation of the demo of
main.go and the plan of a second generation, in which a region changes, a
bucket is added and a database is dropped. When the Go toolchain is not
installed, the same code is mirrored in Python.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(tempfile.gettempdir()) / "foundev-go-helpers"
GO = shutil.which("go")

#: How long an answer of the helper is reused.
TTL = 20.0

#: The helper program: it plans and reconciles, and prints the result.
HELPER = r'''
package main

import (
	"encoding/json"
	"os"
	"sort"
	"strings"
)

func main() {
	var input struct {
		Store      []Resource `json:"store"`
		Desired    []Resource `json:"desired"`
		Generation int        `json:"generation"`
		Mode       string     `json:"mode"`
	}
	_ = json.NewDecoder(os.Stdin).Decode(&input)

	store := NewStore()
	registry := NewSchemaRegistry()
	registry.Register(Schema{
		Kind:     "Bucket",
		Required: []string{"region"},
		Allowed:  map[string][]string{"versioning": {"true", "false"}},
	})
	registry.Register(Schema{
		Kind:     "Database",
		Required: []string{"size"},
		Allowed:  map[string][]string{"size": {"small", "medium", "large"}, "multi_az": {"true", "false"}},
	})
	for _, resource := range input.Store {
		store.Put(resource)
	}

	controller := NewController(store, registry)
	controller.generation = input.Generation
	controller.desired = append([]Resource{}, input.Desired...)

	answer := map[string]any{
		"resources":  store.List(),
		"desired":    controller.Desired(),
		"plan":       Plan(store.List(), controller.Desired()),
		"generation": controller.Generation(),
		"applied":    []Action{},
		"failed":     []FailedAction{},
		"skipped":    []Action{},
		"errors":     []string{},
	}

	schemas := []map[string]any{}
	for kind, schema := range registry.schemas {
		allowed := []string{}
		for field, options := range schema.Allowed {
			allowed = append(allowed, field+"="+strings.Join(options, "|"))
		}
		sort.Strings(allowed)
		schemas = append(schemas, map[string]any{
			"kind":     kind,
			"required": strings.Join(schema.Required, ", "),
			"allowed":  strings.Join(allowed, ", "),
		})
	}
	sort.Slice(schemas, func(i, j int) bool { return schemas[i]["kind"].(string) < schemas[j]["kind"].(string) })
	answer["schemas"] = schemas

	switch input.Mode {
	case "desired":
		generation, err := controller.SetDesired(input.Desired)
		if err != nil {
			answer["error"] = err.Error()
		}
		answer["generation"] = generation
		answer["desired"] = controller.Desired()
		answer["plan"] = Plan(store.List(), controller.Desired())
	case "reconcile":
		result := controller.Reconcile()
		answer["applied"] = result.Applied
		answer["failed"] = result.Failed
		answer["skipped"] = result.Skipped
		answer["errors"] = result.Errors
		answer["resources"] = store.List()
		answer["generation"] = controller.Generation()
	}

	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(answer)
}
'''

#: The desired state of the demo of main.go.
DESIRED_FIRST = [
    {"kind": "Bucket", "name": "assets", "spec": {"region": "eu-west-1", "versioning": True}},
    {"kind": "Database", "name": "orders", "spec": {"size": "small", "multi_az": False}},
]

#: The desired state of the second generation: a region changes, a bucket is
#: added and the database is dropped.
DESIRED_SECOND = [
    {"kind": "Bucket", "name": "assets", "spec": {"region": "eu-central-1", "versioning": True}},
    {"kind": "Bucket", "name": "backups", "spec": {"region": "eu-west-1", "versioning": False}},
]

#: The schemas of main.go.
SCHEMAS = [
    {"kind": "Bucket", "required": "region", "allowed": "versioning=true|false"},
    {"kind": "Database", "required": "size", "allowed": "multi_az=true|false, size=small|medium|large"},
]

#: The schemas of main.go, in the form SchemaRegistry keeps them.
SCHEMA_RULES = {
    "Bucket": {"required": ["region"], "allowed": {"versioning": ["true", "false"]}},
    "Database": {
        "required": ["size"],
        "allowed": {"size": ["small", "medium", "large"], "multi_az": ["true", "false"]},
    },
}

#: The state of the control plane and the cache.
_STATE: dict = {"store": None, "desired": None, "generation": 0, "applied": []}
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
    target = CACHE / f"control-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-control-"))
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


def _run_helper(store: list, desired: list, generation: int, mode: str) -> dict | None:
    """Run the helper and read its answer."""
    binary = _build_helper()
    if binary is None:
        return None
    payload = {
        "store": store, "desired": desired,
        "generation": generation, "mode": mode,
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
    if not isinstance(answer, dict):
        return None
    # an answer with an error is kept: the validation of SetDesired has to
    # reach the caller with the message of the Go code
    return answer


# --------------------------------------------------------------------------
# the fallback: the store, the plan and the reconciler, mirrored
# --------------------------------------------------------------------------

def _go_value(value) -> str:
    """A value of a spec, as fmt.Sprintf("%v", value) writes it in Go."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _validate(resource: dict) -> str | None:
    """The message of SchemaRegistry.Validate, or None when the resource is fine."""
    kind = resource.get("kind", "")
    schema = SCHEMA_RULES.get(kind)
    if schema is None:
        # a kind without a schema is accepted
        return None
    if not str(resource.get("name") or "").strip():
        return f"a {kind} needs a name"
    spec = resource.get("spec") or {}
    for field in schema["required"]:
        value = spec.get(field)
        if value is None or value == "":
            return f'a {kind} needs the field "{field}"'
    for field, options in schema["allowed"].items():
        if field not in spec:
            continue
        if _go_value(spec[field]) not in options:
            return f'the field "{field}" of a {kind} can only be {", ".join(options)}'
    return None


def _fallback(store: list, desired: list, generation: int, mode: str) -> dict:
    """Plan, Reconcile and the Store of control.go, mirrored."""
    resources = {}
    version = 0
    for resource in store:
        version += 1
        resource = dict(resource)
        resource["resourceVersion"] = version
        resources[f"{resource.get('kind')}/{resource.get('name')}"] = resource

    # SetDesired validates the new state before it takes it
    error = ""
    if mode == "desired":
        for resource in desired:
            error = _validate(resource) or ""
            if error:
                break

    def plan_actions() -> list:
        actions = []
        current = {f"{item.get('kind')}/{item.get('name')}": item for item in resources.values()}
        wanted = {f"{item.get('kind')}/{item.get('name')}": item for item in desired}
        for item in desired:
            key = f"{item.get('kind')}/{item.get('name')}"
            if key not in current:
                actions.append({"kind": item.get("kind"), "name": item.get("name"),
                                "operation": "create", "spec": item.get("spec", {})})
            elif current[key].get("spec") != item.get("spec"):
                actions.append({"kind": item.get("kind"), "name": item.get("name"),
                                "operation": "update", "spec": item.get("spec", {})})
        for item in resources.values():
            key = f"{item.get('kind')}/{item.get('name')}"
            if key not in wanted:
                actions.append({"kind": item.get("kind"), "name": item.get("name"),
                                "operation": "delete"})
        actions.sort(key=lambda action: (action.get("kind", ""), action.get("name", "")))
        return actions

    applied = []
    failed = []
    if mode == "reconcile":
        for action in plan_actions():
            key = f"{action.get('kind')}/{action.get('name')}"
            if action.get("operation") == "delete":
                resources.pop(key, None)
            else:
                version += 1
                resources[key] = {
                    "apiVersion": "cloud/v1",
                    "kind": action.get("kind"),
                    "name": action.get("name"),
                    "spec": action.get("spec", {}),
                    "status": "Ready",
                    "conditions": [{"type": "Ready", "status": "True", "reason": "Reconciled"}],
                    "resourceVersion": version,
                }
            applied.append(action)

    answer = {
        "resources": sorted(resources.values(), key=lambda item: (item.get("kind", ""), item.get("name", ""))),
        "desired": desired,
        "plan": plan_actions(),
        "generation": generation,
        "applied": applied,
        "failed": failed,
        "skipped": [],
        "errors": [],
        "schemas": SCHEMAS,
    }
    if error:
        # the desired state was refused: the generation stays where it was
        answer["error"] = error
        answer["desired"] = desired
    elif mode == "desired":
        # SetDesired raises the generation when it takes the new state
        answer["generation"] = generation + 1
    return answer


# --------------------------------------------------------------------------
# the state of the console
# --------------------------------------------------------------------------

def _bootstrap() -> None:
    """The first reconciliation of the demo, and the plan of the second."""
    if _STATE["store"] is not None:
        return
    # the desired state of the demo is set: that is the first generation
    first = _run_helper([], DESIRED_FIRST, 0, "desired")
    if first is None:
        first = _fallback([], DESIRED_FIRST, 0, "desired")
    _STATE["generation"] = first.get("generation", 1)
    # and reconciled
    reconciled = _run_helper([], DESIRED_FIRST, _STATE["generation"], "reconcile")
    if reconciled is None:
        reconciled = _fallback([], DESIRED_FIRST, _STATE["generation"], "reconcile")
    _STATE["store"] = reconciled.get("resources", [])
    _STATE["applied"] = reconciled.get("applied", [])
    # the second generation: a region changes, a bucket is added and the
    # database is dropped
    second = _run_helper(_STATE["store"], DESIRED_SECOND, _STATE["generation"], "desired")
    if second is None:
        second = _fallback(_STATE["store"], DESIRED_SECOND, _STATE["generation"], "desired")
    _STATE["desired"] = DESIRED_SECOND
    _STATE["generation"] = second.get("generation", _STATE["generation"] + 1)


def _answer(force: bool = False) -> dict:
    """The state of the control plane."""
    _bootstrap()
    key = json.dumps([_STATE["store"], _STATE["desired"], _STATE["generation"]], sort_keys=True)
    now = time.time()
    if (not force and _ANSWER["data"] is not None and _ANSWER["key"] == key
            and now - _ANSWER["at"] < TTL):
        return _ANSWER["data"]
    answer = _run_helper(_STATE["store"], _STATE["desired"], _STATE["generation"], "summary")
    if answer is None:
        answer = _fallback(_STATE["store"], _STATE["desired"], _STATE["generation"], "summary")
    answer["applied"] = _STATE["applied"]
    _ANSWER.update({"at": now, "key": key, "data": answer})
    return answer


def _resource_rows(answer: dict) -> list:
    """The resources of the store."""
    rows = []
    for resource in answer.get("resources", []):
        condition = ""
        conditions = resource.get("conditions") or []
        if conditions:
            condition = f"{conditions[0].get('type', '')}={conditions[0].get('status', '')}"
        rows.append({
            "Kind": resource.get("kind", ""),
            "Name": resource.get("name", ""),
            "Status": resource.get("status", ""),
            "Version": resource.get("resourceVersion", 0),
            "Condition": condition,
        })
    return rows


def _desired_rows(answer: dict) -> list:
    """The desired state."""
    rows = []
    for resource in answer.get("desired", []):
        spec = resource.get("spec", {})
        rows.append({
            "Kind": resource.get("kind", ""),
            "Name": resource.get("name", ""),
            "Spec": ", ".join(f"{field}={value}" for field, value in sorted(spec.items())),
        })
    return rows


def _action_rows(actions: list) -> list:
    """The actions of a plan."""
    return [
        {
            "Kind": action.get("kind", ""),
            "Name": action.get("name", ""),
            "Operation": action.get("operation", ""),
        }
        for action in actions
    ]


def _schema_rows(answer: dict) -> list:
    """The schemas of the kinds the control plane knows."""
    return [
        {
            "Kind": schema.get("kind", ""),
            "Required": schema.get("required", ""),
            "Allowed values": schema.get("allowed", ""),
        }
        for schema in answer.get("schemas", SCHEMAS)
    ]


def summary() -> dict:
    """The resources, the desired state and the plan."""
    answer = _answer()
    resources = answer.get("resources", [])
    plan = answer.get("plan", [])
    applied = answer.get("applied", [])
    kinds = {resource.get("kind", "") for resource in resources}
    return {
        "stats": {
            "resources": len(resources),
            "desired": len(answer.get("desired", [])),
            "generation": answer.get("generation", 0),
            "applied": len(applied),
            "pending": len(plan),
            "kinds": len(kinds),
            "schemas": len(answer.get("schemas", SCHEMAS)),
        },
        "series": [
            {"label": row["Kind"], "value": row["Version"]} for row in _resource_rows(answer)
        ],
        "lists": {
            "resources": _resource_rows(answer),
            "desired": _desired_rows(answer),
            "plan": _action_rows(plan),
            "applied": _action_rows(applied),
            "schemas": _schema_rows(answer),
        },
    }


# --------------------------------------------------------------------------
# the endpoints
# --------------------------------------------------------------------------

def handle(method, path, payload, user):
    """The endpoints of the control plane."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/resources":
        return 200, {"resources": _resource_rows(_answer())}
    if method == "GET" and path == "/api/plan":
        return 200, {"plan": _action_rows(_answer().get("plan", []))}
    if method == "GET" and path == "/api/schemas":
        return 200, {"schemas": _schema_rows(_answer())}
    if method == "POST" and path == "/api/desired":
        body = payload or {}
        raw = body.get("resources")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                return 400, {"error": "the resources are not valid JSON"}
        if not isinstance(raw, list) or not raw:
            return 400, {"error": "a list of resources is needed"}
        _bootstrap()
        answer = _run_helper(_STATE["store"], raw, _STATE["generation"], "desired")
        if answer is None:
            answer = _fallback(_STATE["store"], raw, _STATE["generation"], "desired")
        if answer.get("error"):
            return 400, {"error": answer["error"]}
        _STATE["desired"] = raw
        _STATE["generation"] = answer.get("generation", _STATE["generation"])
        _ANSWER["data"] = None
        return 200, {
            "generation": _STATE["generation"],
            "planned": len(answer.get("plan", [])),
        }
    if method == "POST" and path == "/api/reconcile":
        _bootstrap()
        answer = _run_helper(_STATE["store"], _STATE["desired"], _STATE["generation"], "reconcile")
        if answer is None:
            answer = _fallback(_STATE["store"], _STATE["desired"], _STATE["generation"], "reconcile")
        _STATE["store"] = answer.get("resources", _STATE["store"])
        _STATE["applied"] = answer.get("applied", [])
        _ANSWER["data"] = None
        return 200, {
            "applied": len(answer.get("applied", [])),
            "failed": len(answer.get("failed", [])),
            "resources": len(_STATE["store"]),
        }
    return 404, {"error": "not found"}
