"""The API of the campaign planner: the sends, the events and the funnel.

The numbers come from the Go code of the project. A helper program is built
from the sources of the project (campaign.go, main.go and go.mod) with a
`main` that makes a Campaign with NewCampaign, plans the sends with Plan,
records the opens, the clicks and the reports with RecordOpen, RecordClick
and RecordReport and computes the funnel with Summary. No mail is sent: the
project simulates the delivery, so the events of the campaigns of the console
are recorded by the helper the same way the demo of main.go records them.
When the Go toolchain is not installed, the same code is mirrored in Python.
"""

from __future__ import annotations

import hashlib
import json
import secrets
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

#: The helper program: it plans the campaigns and prints them as JSON.
HELPER = r'''
package main

import (
	"encoding/json"
	"os"
)

func main() {
	var input struct {
		Campaigns []struct {
			Name     string `json:"name"`
			Template string `json:"template"`
			Targets  []string `json:"targets"`
			Opened   []int   `json:"opened"`
			Clicked  []int   `json:"clicked"`
			Reported []int   `json:"reported"`
		} `json:"campaigns"`
	}
	_ = json.NewDecoder(os.Stdin).Decode(&input)

	campaigns := []map[string]any{}
	for _, item := range input.Campaigns {
		campaign := NewCampaign(item.Name, item.Template)
		plan := Plan(campaign, item.Targets)
		for _, index := range item.Opened {
			if index >= 0 && index < len(plan) {
				RecordOpen(campaign, plan[index].TrackingID)
			}
		}
		for _, index := range item.Clicked {
			if index >= 0 && index < len(plan) {
				RecordClick(campaign, plan[index].TrackingID)
			}
		}
		for _, index := range item.Reported {
			if index >= 0 && index < len(plan) {
				RecordReport(campaign, plan[index].TrackingID)
			}
		}
		// the sends of the campaign, in the order of the plan, with the
		// events that were recorded on them
		sends := []Send{}
		for _, send := range plan {
			if current, ok := campaign.Sends[send.TrackingID]; ok {
				sends = append(sends, *current)
			}
		}
		campaigns = append(campaigns, map[string]any{
			"name":     campaign.Name,
			"template": campaign.Template,
			"plan":     sends,
			"summary":  Summary(campaign),
		})
	}

	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(map[string]any{"campaigns": campaigns})
}
'''

#: The campaigns of the console. The events are recorded, no mail is sent.
CAMPAIGNS = [
    {
        "name": "Q3 awareness",
        "template": "password-reset",
        "targets": [
            "ana@example.com", "bo@example.com", "cat@example.com",
            "dan@example.com", "eve@example.com", "fay@example.com",
        ],
        "opened": [0, 1, 2, 4],
        "clicked": [1, 2],
        "reported": [2, 5],
    },
    {
        "name": "Q2 phishing drill",
        "template": "invoice-attachment",
        "targets": [
            "gita@example.com", "hugo@example.com",
            "iris@example.com", "juan@example.com",
        ],
        "opened": [0, 1],
        "clicked": [1],
        "reported": [3],
    },
    {
        "name": "Vendor access review",
        "template": "shared-document",
        "targets": ["kira@example.com", "lars@example.com", "mira@example.com"],
        "opened": [0],
        "clicked": [],
        "reported": [],
    },
]

#: The campaigns of the console and the cache.
_STATE: dict = {"campaigns": None}
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
    target = CACHE / f"gophish-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-gophish-"))
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


def _run_helper(campaigns: list) -> dict | None:
    """Plan the campaigns with the code of the project."""
    binary = _build_helper()
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [str(binary)], input=json.dumps({"campaigns": campaigns}),
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
    return answer


# --------------------------------------------------------------------------
# the fallback: Plan, RecordOpen, RecordClick, RecordReport and Summary
# --------------------------------------------------------------------------

def _fallback(campaigns: list) -> dict:
    """The campaign code of the project, mirrored in Python."""
    result = []
    for item in campaigns:
        plan = []
        for target in item.get("targets", []):
            email = str(target).strip()
            if not email:
                continue
            plan.append({
                "tracking_id": secrets.token_hex(8),
                "email": email,
                "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "opened": False,
                "clicked": False,
                "reported": False,
            })
        for index in item.get("opened", []):
            if 0 <= index < len(plan):
                plan[index]["opened"] = True
        for index in item.get("clicked", []):
            if 0 <= index < len(plan):
                plan[index]["opened"] = True
                plan[index]["clicked"] = True
        for index in item.get("reported", []):
            if 0 <= index < len(plan):
                plan[index]["reported"] = True
        sent = len(plan)
        opened = sum(1 for send in plan if send["opened"])
        clicked = sum(1 for send in plan if send["clicked"])
        reported = sum(1 for send in plan if send["reported"])
        summary = {
            "campaign": item.get("name", ""),
            "sent": sent,
            "opened": opened,
            "clicked": clicked,
            "reported": reported,
            "open_rate": opened / sent if sent else 0.0,
            "click_rate": clicked / sent if sent else 0.0,
            "report_rate": reported / opened if opened else 0.0,
        }
        result.append({
            "name": item.get("name", ""),
            "template": item.get("template", ""),
            "plan": plan,
            "summary": summary,
        })
    return {"campaigns": result}


# --------------------------------------------------------------------------
# the state of the console
# --------------------------------------------------------------------------

def _campaigns() -> list:
    """The campaigns of the console."""
    if _STATE["campaigns"] is None:
        _STATE["campaigns"] = [dict(item) for item in CAMPAIGNS]
    return _STATE["campaigns"]


def _answer(force: bool = False) -> dict:
    """The campaigns, planned by the code of the project."""
    campaigns = _campaigns()
    key = json.dumps(campaigns, sort_keys=True)
    now = time.time()
    if (not force and _ANSWER["data"] is not None and _ANSWER["key"] == key
            and now - _ANSWER["at"] < TTL):
        return _ANSWER["data"]
    answer = _run_helper(campaigns) or _fallback(campaigns)
    _ANSWER.update({"at": now, "key": key, "data": answer})
    return answer


def _rate(value) -> float:
    """A rate of the funnel, in percent."""
    try:
        return round(float(value) * 100, 1)
    except (TypeError, ValueError):
        return 0.0


def _totals(answer: dict) -> dict:
    """The numbers of all the campaigns together."""
    sent = 0
    opened = 0
    clicked = 0
    reported = 0
    for campaign in answer.get("campaigns", []):
        summary = campaign.get("summary", {})
        sent += int(summary.get("sent", 0))
        opened += int(summary.get("opened", 0))
        clicked += int(summary.get("clicked", 0))
        reported += int(summary.get("reported", 0))
    return {
        "sent": sent,
        "opened": opened,
        "clicked": clicked,
        "reported": reported,
        "open_rate": opened / sent if sent else 0.0,
        "click_rate": clicked / sent if sent else 0.0,
        "report_rate": reported / opened if opened else 0.0,
    }


def _campaign_rows(answer: dict) -> list:
    """The campaigns with their funnel."""
    rows = []
    for campaign in answer.get("campaigns", []):
        summary = campaign.get("summary", {})
        rows.append({
            "Campaign": campaign.get("name", ""),
            "Template": campaign.get("template", ""),
            "Sends": summary.get("sent", 0),
            "Opened": summary.get("opened", 0),
            "Clicked": summary.get("clicked", 0),
            "Reported": summary.get("reported", 0),
            "Open rate": _rate(summary.get("open_rate", 0)),
        })
    return rows


def _send_rows(answer: dict) -> list:
    """Every send of every campaign."""
    rows = []
    for campaign in answer.get("campaigns", []):
        for send in campaign.get("plan", []):
            rows.append({
                "Campaign": campaign.get("name", ""),
                "Email": send.get("email", ""),
                "Tracking id": send.get("tracking_id", ""),
                "Opened": "yes" if send.get("opened") else "no",
                "Clicked": "yes" if send.get("clicked") else "no",
                "Reported": "yes" if send.get("reported") else "no",
                "Sent at": send.get("sent_at", ""),
            })
    return rows


def _event_rows(answer: dict) -> list:
    """The events of the sends: an open, a click and a report."""
    rows = []
    for campaign in answer.get("campaigns", []):
        for send in campaign.get("plan", []):
            for event, field in (("open", "opened"), ("click", "clicked"), ("report", "reported")):
                if send.get(field):
                    rows.append({
                        "Campaign": campaign.get("name", ""),
                        "Email": send.get("email", ""),
                        "Event": event,
                        "Tracking id": send.get("tracking_id", ""),
                    })
    return rows


def summary() -> dict:
    """The campaigns, the sends and the funnel."""
    answer = _answer()
    totals = _totals(answer)
    return {
        "stats": {
            "campaigns": len(answer.get("campaigns", [])),
            "sent": totals["sent"],
            "opened": totals["opened"],
            "clicked": totals["clicked"],
            "reported": totals["reported"],
            "open_rate": _rate(totals["open_rate"]),
            "click_rate": _rate(totals["click_rate"]),
            "report_rate": _rate(totals["report_rate"]),
        },
        "series": [
            {"label": row["Campaign"], "value": row["Opened"]} for row in _campaign_rows(answer)
        ],
        "lists": {
            "campaigns": _campaign_rows(answer),
            "sends": _send_rows(answer),
            "events": _event_rows(answer),
        },
    }


# --------------------------------------------------------------------------
# the endpoints
# --------------------------------------------------------------------------

def handle(method, path, payload, user):
    """The endpoints of the campaign planner."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/campaigns":
        return 200, {"campaigns": _campaign_rows(_answer())}
    if method == "GET" and path == "/api/sends":
        return 200, {"sends": _send_rows(_answer())}
    if method == "GET" and path == "/api/events":
        return 200, {"events": _event_rows(_answer())}
    if method == "POST" and path == "/api/campaign":
        body = payload or {}
        name = str(body.get("name") or "Awareness campaign").strip() or "Awareness campaign"
        template = str(body.get("template") or "password-reset").strip() or "password-reset"
        raw = body.get("targets")
        if isinstance(raw, str):
            targets = [part.strip() for part in raw.split(",") if part.strip()]
        elif isinstance(raw, list):
            targets = [str(part).strip() for part in raw if str(part).strip()]
        else:
            targets = []
        if not targets:
            return 400, {"error": "a campaign needs at least one target"}
        # the demo of main.go: the first mail is opened, the second one is
        # clicked and reported
        campaign = {
            "name": name,
            "template": template,
            "targets": targets,
            "opened": [0],
            "clicked": [1] if len(targets) > 1 else [],
            "reported": [1] if len(targets) > 1 else [],
        }
        _STATE["campaigns"] = _campaigns() + [campaign]
        answer = _answer(force=True)
        planned = answer.get("campaigns", [])[-1] if answer.get("campaigns") else {}
        return 200, {
            "campaign": name,
            "template": template,
            "sent": planned.get("summary", {}).get("sent", 0),
            "opened": planned.get("summary", {}).get("opened", 0),
            "clicked": planned.get("summary", {}).get("clicked", 0),
            "reported": planned.get("summary", {}).get("reported", 0),
        }
    return 404, {"error": "not found"}
