"""API of the Tekton pipeline engine: tasks, pipelines and runs."""

from __future__ import annotations


def _state():
    return {
        "tasks": [
            {"name": "build", "steps": 1},
            {"name": "test", "steps": 1},
            {"name": "scan", "steps": 1},
        ],
        "statuses": [
            {"task": "build", "status": "succeeded", "steps": 1, "duration": "0s"},
            {"task": "test", "status": "succeeded", "steps": 1, "duration": "0s"},
            {"task": "scan", "status": "succeeded", "steps": 1, "duration": "0s"},
        ],
    }


def summary() -> dict:
    state = _state()
    return {
        "stats": {
            "tasks": len(state["tasks"]),
            "resolved": len(state["statuses"]),
            "steps": sum(t["steps"] for t in state["tasks"]),
            "pipelines": 1,
        },
        "lists": {
            "tasks": state["tasks"],
            "statuses": state["statuses"],
        },
    }


def handle(method, path, payload, user):
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    return 404, {"error": "not found"}
