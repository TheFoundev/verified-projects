"""The API of the project: the log of the honeypot.

The numbers come from the real honeypot: the server is started, a few clients
log in and ask for things, and the events of every session are read back from
the log of the run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline import run  # noqa: E402

#: Where the honeypot writes the log of the run.
BASE_DIR = ROOT / "artifacts"
LOG = BASE_DIR / "events.jsonl"

#: The summary of the last run, kept for the life of the process.
_STATE: dict = {}


def state() -> dict:
    """Let the clients in and keep the events of the run."""
    if "run" not in _STATE:
        result = run(quick=False, base_dir=str(BASE_DIR))
        events = []
        if LOG.exists():
            for line in LOG.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        _STATE["run"] = result
        _STATE["events"] = events
    return _STATE


def sessions() -> list:
    """The logins that were tried, with the password and the address."""
    return [
        {
            "user": event.get("user", ""),
            "password": event.get("password", ""),
            "from": event.get("source", ""),
        }
        for event in state()["events"] if event.get("kind") == "login"
    ]


def commands_per_session() -> list:
    """How many commands every session asked for."""
    counts: dict = {}
    for event in state()["events"]:
        if event.get("kind") == "command":
            user = event.get("user", "")
            counts[user] = counts.get(user, 0) + 1
    return [{"label": user, "value": count} for user, count in counts.items()]


def summary() -> dict:
    """The state of the last run: the sessions, the commands and the users."""
    current = state()
    result = current["run"]
    return {
        "stats": {
            "sessions": result["sessions"],
            "commands": result["commands"],
            "unique_users": result["unique_users"],
            "port": result["port"],
        },
        "series": commands_per_session(),
        "lists": {
            "events": [
                {
                    "kind": event.get("kind", ""),
                    "user": event.get("user", ""),
                    "command": event.get("command", ""),
                    "from": event.get("source", ""),
                }
                for event in current["events"]
            ],
            "sessions": sessions(),
        },
    }


def handle(method, path, payload, user):
    """The read and the write endpoints of the project."""
    if method == "GET" and path == "/api/events":
        return 200, {"events": summary()["lists"]["events"]}
    if method == "GET" and path == "/api/sessions":
        return 200, {"sessions": sessions()}
    if method == "POST" and path == "/api/run":
        _STATE.pop("run", None)
        _STATE.pop("events", None)
        state()
        return 200, summary()["stats"]
    return 404, {"error": "not found"}
