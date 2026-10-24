"""The API of the project: the plan of a container and what the runner did.

The plan comes from the real planner of the project: a `ContainerSpec` is
turned into the steps of the plan, the namespace mask is built from the flags
of the kernel, the root filesystem is generated and the runner writes the
cgroup limits and starts the command when the machine gives it the rights.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime import ContainerSpec, namespace_flags, plan_container  # noqa: E402
from src.pipeline import (  # noqa: E402
    NAMESPACE_FLAGS,
    effective_uid,
    execute,
    make_rootfs,
    namespace_mask,
    run,
)

#: Where the plan and the root filesystem of the container are written.
BASE_DIR = ROOT / "artifacts"

#: The plan and the result of the last run.
_STATE: dict = {}


def state(force: bool = False) -> dict:
    """Plan the container of the project and keep the result of the run."""
    if force or "run" not in _STATE or "plan" not in _STATE:
        if (BASE_DIR / "plan.json").exists():
            try:
                result = _STATE.get("run") or run(quick=False, base_dir=str(BASE_DIR))
                plan = json.loads((BASE_DIR / "plan.json").read_text(encoding="utf-8"))
                _STATE["run"] = result
                _STATE["plan"] = plan
            except Exception:
                result = run(quick=False, base_dir=str(BASE_DIR))
                plan = json.loads((BASE_DIR / "plan.json").read_text(encoding="utf-8"))
                _STATE["run"] = result
                _STATE["plan"] = plan
        else:
            result = run(quick=False, base_dir=str(BASE_DIR))
            plan = json.loads((BASE_DIR / "plan.json").read_text(encoding="utf-8"))
            _STATE["run"] = result
            _STATE["plan"] = plan
    return _STATE


def plan_steps(plan: dict) -> list:
    """The steps of the plan, in the order the runner walks them."""
    result = []
    for step in plan["steps"]:
        detail = step["detail"]
        # Remove local project paths for cleaner display
        detail = detail.replace(str(ROOT), ".")
        detail = detail.replace(str(BASE_DIR), "artifacts")
        result.append({"step": step["kind"], "what": detail})
    return result


def namespace_rows(plan: dict) -> list:
    """The namespaces of the container and the bit each of them adds."""
    return [
        {"namespace": name, "flag": hex(NAMESPACE_FLAGS[name])}
        for name in plan["namespaces"]
    ]


def summary() -> dict:
    """The state of the last plan: the steps, the namespaces and the limits."""
    current = state(force=True)
    result = current["run"]
    plan = current["plan"]
    return {
        "stats": {
            "steps": result["steps"],
            "namespaces": result["namespaces"],
            "memory_mb": result["memory_mb"],
            "mode": "dry run" if result["dry_run"] else "executed",
        },
        "series": [
            {"label": name, "value": NAMESPACE_FLAGS[name]} for name in plan["namespaces"]
        ],
        "lists": {
            "plan": plan_steps(plan),
            "namespaces": namespace_rows(plan),
        },
    }


def plan_container_of(payload: dict) -> dict:
    """Plan a container with the limits of the request and run the steps.

    The root filesystem is generated, the cgroup limits are written when the
    machine allows it and the command is started when the rights are there.
    """
    name = str(payload.get("name") or "orders").strip() or "orders"
    spec = ContainerSpec(
        name=name,
        memory_mb=int(payload.get("memory_mb") or 256),
        cpu_percent=int(payload.get("cpu_percent") or 25),
    )
    steps = plan_container(spec, BASE_DIR)
    make_rootfs(BASE_DIR, spec)
    # The namespaces need the rights, so the run stays a dry run without them.
    dry_run = effective_uid() != 0
    status = execute(spec, steps, BASE_DIR / "runtime.log", dry_run=dry_run)
    plan = {
        "spec": {
            "name": spec.name,
            "rootfs": spec.rootfs,
            "memory_mb": spec.memory_mb,
            "cpu_percent": spec.cpu_percent,
            "command": list(spec.command),
        },
        "namespaces": namespace_flags(spec),
        "namespace_mask": namespace_mask(spec),
        "steps": [step.to_dict() for step in steps],
        "status": status,
    }
    (BASE_DIR / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    _STATE["run"] = {
        "steps": len(steps),
        "namespaces": len(plan["namespaces"]),
        "memory_mb": spec.memory_mb,
        "cgroups": 1,
        "executed": status["executed"],
        "dry_run": dry_run,
        "plan": str(BASE_DIR / "plan.json"),
    }
    _STATE["plan"] = plan
    return plan


def handle(method, path, payload, user):
    """The read and the write endpoints of the project."""
    if method == "GET" and path == "/api/plan":
        current = state(force=True)
        return 200, {"plan": plan_steps(current["plan"]), "status": current["run"]}
    if method == "POST" and path == "/api/plan":
        plan = plan_container_of(payload or {})
        state(force=True)
        s = summary()
        return 200, {
            "name": plan["spec"]["name"],
            "steps": len(plan["steps"]),
            "namespaces": len(plan["namespaces"]),
            "memory_mb": plan["spec"]["memory_mb"],
            "executed": plan["status"]["executed"],
            "stats": s.get("stats"),
            "lists": s.get("lists"),
        }
    return 404, {"error": "not found"}
