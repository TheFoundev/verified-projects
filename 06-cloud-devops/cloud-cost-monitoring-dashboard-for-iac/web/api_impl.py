"""The API of the cost tool: the price of a Terraform plan.

The numbers come from the Go code of the project. A helper program is built
from the sources of the project (cost.go, main.go, api.go and go.mod) with a
`main` that calls ParsePlan and EstimateAll and prints the Summary as JSON.
The plan of the example is the one that is written in main.go. When the Go
toolchain is not installed, the same plan is parsed and priced here, with the
price tables and the rates that are written in cost.go.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(tempfile.gettempdir()) / "foundev-go-helpers"
GO = shutil.which("go")

#: The helper program: it prices a plan and prints the summary as JSON.
HELPER = r'''
package main

import (
	"encoding/json"
	"io"
	"os"
)

func main() {
	data, err := io.ReadAll(os.Stdin)
	if err != nil {
		json.NewEncoder(os.Stdout).Encode(map[string]any{"error": err.Error()})
		os.Exit(1)
	}
	if len(data) == 0 {
		// the example plan of main.go
		data = []byte(examplePlan)
	}
	resources, err := ParsePlan(data)
	if err != nil {
		json.NewEncoder(os.Stdout).Encode(map[string]any{"error": err.Error()})
		os.Exit(1)
	}
	json.NewEncoder(os.Stdout).Encode(EstimateAll(resources))
}
'''

#: The summary of the plan that is on screen.
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
    target = CACHE / f"cost-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-cost-"))
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


def _run_helper(plan: str) -> dict | None:
    """Run the helper on a plan and read its JSON answer."""
    binary = _build_helper()
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [str(binary)], input=plan, capture_output=True, text=True, timeout=120,
        )
    except Exception:
        return None
    try:
        answer = json.loads(result.stdout)
    except ValueError:
        return None
    if not isinstance(answer, dict):
        return None
    if answer.get("error"):
        # the plan was refused by ParsePlan of the project
        raise ValueError(answer["error"])
    return answer


def _example_plan() -> str:
    """The example plan of the project, as it is written in main.go."""
    text = (ROOT / "main.go").read_text(encoding="utf-8")
    match = re.search(r"const examplePlan = `(.*?)`", text, re.S)
    return match.group(1) if match else "{}"


def _number(value) -> float:
    """numberValue of cost.go."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _price_tables() -> dict:
    """The price tables of cost.go."""
    text = (ROOT / "cost.go").read_text(encoding="utf-8")
    tables = {}
    for name in ("instancePrices", "googleComputePrices", "databasePrices"):
        match = re.search(name + r"\s*=\s*map\[string\]float64\{(.*?)\}", text, re.S)
        tables[name] = {}
        if match:
            tables[name] = {
                key: float(value)
                for key, value in re.findall(r'"([^"]+)":\s*([0-9.]+)', match.group(1))
            }
    return tables


def _rates() -> dict:
    """The rates of EstimateResource that are not in a table."""
    text = (ROOT / "cost.go").read_text(encoding="utf-8")
    rates = {"storage": 0.0, "ebs": 0.0, "gcp_sql": 0.0}
    storage = re.search(r"monthly = storage \* ([0-9.]+)", text)
    if storage:
        rates["storage"] = float(storage.group(1))
    ebs = re.search(r'numberValue\(resource\.Values\["size"\]\) \* ([0-9.]+)', text)
    if ebs:
        rates["ebs"] = float(ebs.group(1))
    sql = re.search(r"google_sql_database_instance\":\s*\n\s*monthly = ([0-9.]+) \* count", text)
    if sql:
        rates["gcp_sql"] = float(sql.group(1))
    return rates


def _parse_plan(document) -> list:
    """ParsePlan of cost.go: the resources of the root module and the modules."""
    if not isinstance(document, dict):
        raise ValueError("parse plan: the document is not an object")
    planned = document.get("planned_values")
    planned = planned if isinstance(planned, dict) else {}
    root = planned.get("root_module")
    root = root if isinstance(root, dict) else {}
    raw = root.get("resources")
    if raw is None:
        raise ValueError("no planned resources found")
    resources = list(raw)
    for child in root.get("child_modules") or []:
        if isinstance(child, dict):
            resources += list(child.get("resources") or [])
    out = []
    for entry in resources:
        if not isinstance(entry, dict):
            continue
        values = entry.get("values")
        out.append({
            "type": entry.get("type") if isinstance(entry.get("type"), str) else "",
            "name": entry.get("name") if isinstance(entry.get("name"), str) else "",
            "values": values if isinstance(values, dict) else {},
        })
    return out


def _estimate_resource(resource: dict, tables: dict, rates: dict) -> float:
    """EstimateResource of cost.go."""
    values = resource.get("values", {})
    count = _number(values.get("count"))
    if count == 0:
        count = 1
    kind = resource.get("type", "")
    if kind == "aws_instance":
        return tables["instancePrices"].get(values.get("instance_type", ""), 0.0) * count
    if kind == "aws_db_instance":
        return tables["databasePrices"].get(values.get("instance_class", ""), 0.0)
    if kind == "aws_s3_bucket":
        storage = _number(values.get("storage_gb"))
        if storage == 0:
            storage = 10
        return storage * rates["storage"]
    if kind == "aws_ebs_volume":
        return _number(values.get("size")) * rates["ebs"]
    if kind == "google_compute_instance":
        return tables["googleComputePrices"].get(values.get("machine_type", ""), 0.0) * count
    if kind == "google_sql_database_instance":
        return rates["gcp_sql"] * count
    return 0.0


def _estimate_in_python(plan: str) -> dict:
    """ParsePlan and EstimateAll of cost.go, mirrored."""
    try:
        document = json.loads(plan)
    except ValueError as error:
        raise ValueError(f"parse plan: {error}")
    resources = _parse_plan(document)
    tables = _price_tables()
    rates = _rates()
    rows = []
    by_type = {}
    by_provider = {}
    unpriced = []
    total = 0.0
    for resource in resources:
        monthly = _estimate_resource(resource, tables, rates)
        rows.append({"type": resource["type"], "name": resource["name"], "monthly": monthly})
        total += monthly
        by_type[resource["type"]] = by_type.get(resource["type"], 0.0) + monthly
        parts = resource["type"].split("_", 1)
        provider = parts[0] if len(parts) > 1 else "other"
        by_provider[provider] = by_provider.get(provider, 0.0) + monthly
        if monthly == 0:
            unpriced.append(resource["type"] + "/" + resource["name"])
    return {
        "resources": rows,
        "total": total,
        "by_type": by_type,
        "by_provider": by_provider,
        "unpriced": sorted(unpriced),
    }


def _estimate(plan: str) -> dict:
    """Price a plan with the code of the project."""
    answer = _run_helper(plan)
    if answer:
        return answer
    return _estimate_in_python(plan)


def _summary() -> dict:
    """The summary of the plan that is on screen."""
    if "summary" not in _STATE:
        _STATE["summary"] = _estimate(_example_plan())
    return _STATE["summary"]


def _money(value) -> float:
    """The monthly price, rounded to the cent."""
    return round(float(value or 0.0), 2)


def summary() -> dict:
    """The cost of the plan: the total, the counts and the totals."""
    data = _summary()
    by_type = data.get("by_type", {})
    by_provider = data.get("by_provider", {})
    resources = data.get("resources", [])
    unpriced = data.get("unpriced", [])
    return {
        "stats": {
            "total": _money(data.get("total", 0.0)),
            "resources": len(resources),
            "types": len(by_type),
            "providers": len(by_provider),
            "unpriced": len(unpriced),
        },
        "series": [
            {"label": name, "value": _money(value)} for name, value in sorted(by_type.items())
        ],
        "lists": {
            "resources": [
                {
                    "Type": row.get("type", ""),
                    "Name": row.get("name", ""),
                    "Monthly (USD)": _money(row.get("monthly", 0.0)),
                }
                for row in resources
            ],
            "by_type": [
                {"Type": name, "Monthly (USD)": _money(value)}
                for name, value in sorted(by_type.items())
            ],
            "by_provider": [
                {"Provider": name, "Monthly (USD)": _money(value)}
                for name, value in sorted(by_provider.items())
            ],
        },
    }


def handle(method, path, payload, user):
    """The read and the write endpoints of the cost tool."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/resources":
        return 200, {"resources": summary()["lists"]["resources"]}
    if method == "GET" and path == "/api/totals":
        data = summary()
        return 200, {
            "by_type": data["lists"]["by_type"],
            "by_provider": data["lists"]["by_provider"],
            "total": data["stats"]["total"],
        }
    if method == "POST" and path == "/api/estimate":
        body = payload or {}
        plan = body.get("plan")
        if not isinstance(plan, str) or not plan.strip():
            return 400, {"error": "the request has to carry a plan document"}
        try:
            data = _estimate(plan)
        except ValueError as error:
            return 400, {"error": str(error)}
        _STATE["summary"] = data
        return 200, {
            "total": _money(data.get("total", 0.0)),
            "resources": len(data.get("resources", [])),
            "by_type": len(data.get("by_type", {})),
            "by_provider": len(data.get("by_provider", {})),
            "unpriced": len(data.get("unpriced", [])),
        }
    return 404, {"error": "not found"}
