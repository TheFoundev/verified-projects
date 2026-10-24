"""The API of the firewall: the rule set and the findings of a request.

The numbers come from the Go code of the project. A helper program is built
from the sources of the project (waf.go, main.go and go.mod) with a `main`
that reads the rules, inspects a request with Inspect and puts the same
decision in front of a handler with Middleware, and prints the result as
JSON. When the Go toolchain is not installed, the rules are read from waf.go
and the same patterns are applied here.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(tempfile.gettempdir()) / "foundev-go-helpers"
GO = shutil.which("go")

#: The request the console shows when nothing was inspected yet.
SAMPLE = {"method": "GET", "path": "/items", "query": "id=1 union select 1", "body": ""}

#: The helper program: it inspects a request and prints the result as JSON.
HELPER = r'''
package main

import (
	"encoding/json"
	"flag"
	"net/http"
	"net/http/httptest"
	"os"
)

func main() {
	method := flag.String("method", "GET", "the method of the request")
	path := flag.String("path", "/items", "the path of the request")
	query := flag.String("query", "id=1 union select 1", "the query of the request")
	body := flag.String("body", "", "the body of the request")
	flag.Parse()

	rulesOut := []map[string]any{}
	for _, rule := range rules {
		rulesOut = append(rulesOut, map[string]any{
			"id":       rule.ID,
			"category": rule.Category,
			"severity": rule.Severity,
			"pattern":  rule.Pattern.String(),
		})
	}

	request := Request{Method: *method, Path: *path, Query: *query, Body: *body}
	findings := Inspect(request)
	decision := "allow"
	if Blocked(findings) {
		decision = "block"
	}

	// the same decision in front of a handler
	target := "http://127.0.0.1" + *path
	if *query != "" {
		target = target + "?" + *query
	}
	httpRequest, err := http.NewRequest(*method, target, http.NoBody)
	if err != nil {
		json.NewEncoder(os.Stdout).Encode(map[string]any{"error": err.Error()})
		os.Exit(1)
	}
	recorder := httptest.NewRecorder()
	handler := Middleware(http.HandlerFunc(func(writer http.ResponseWriter, r *http.Request) {
		writer.WriteHeader(http.StatusOK)
	}))
	handler.ServeHTTP(recorder, httpRequest)

	json.NewEncoder(os.Stdout).Encode(map[string]any{
		"rules":    rulesOut,
		"findings": findings,
		"decision": decision,
		"score":    Score(findings),
		"status":   recorder.Code,
		"request":  request,
	})
}
'''

#: The last inspection.
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
    target = CACHE / f"coraza-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-coraza-"))
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


def _run_helper(request: dict) -> dict | None:
    """Run the helper on a request and read its JSON answer."""
    binary = _build_helper()
    if binary is None:
        return None
    arguments = [
        "-method", request["method"],
        "-path", request["path"],
        "-query", request["query"],
        "-body", request["body"],
    ]
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


def _rules_from_source() -> list:
    """The rules of waf.go, read from the source."""
    text = (ROOT / "waf.go").read_text(encoding="utf-8")
    found = re.findall(
        r'\{"(CRS-\d+)",\s*"([a-z]+)",\s*(\d+),\s*regexp\.MustCompile\(`([^`]*)`\)\}',
        text,
    )
    return [
        {"id": rule_id, "category": category, "severity": int(severity), "pattern": pattern}
        for rule_id, category, severity, pattern in found
    ]


def _inspect_in_python(request: dict) -> dict:
    """Inspect, Score and Blocked of waf.go, mirrored."""
    rules = _rules_from_source()
    findings = []
    for rule in rules:
        pattern = re.compile(rule["pattern"])
        for target in (request["path"], request["query"], request["body"]):
            if not target:
                continue
            match = pattern.search(target)
            if match:
                findings.append({
                    "rule": rule["id"],
                    "category": rule["category"],
                    "severity": rule["severity"],
                    "evidence": match.group(0),
                })
                break
    score = sum(finding["severity"] for finding in findings)
    decision = "block" if score >= 2 else "allow"

    # the middleware decodes the escapes once before it looks at the request
    decoded = {
        "method": request["method"],
        "path": unquote(request["path"]),
        "query": unquote(request["query"]),
        "body": unquote(request["body"]),
    }
    decoded_findings = []
    for rule in rules:
        pattern = re.compile(rule["pattern"])
        for target in (decoded["path"], decoded["query"], decoded["body"]):
            if not target:
                continue
            match = pattern.search(target)
            if match:
                decoded_findings.append({
                    "rule": rule["id"],
                    "category": rule["category"],
                    "severity": rule["severity"],
                    "evidence": match.group(0),
                })
                break
    decoded_score = sum(finding["severity"] for finding in decoded_findings)
    status = 403 if decoded_score >= 2 else 200

    return {
        "rules": rules,
        "findings": findings,
        "decision": decision,
        "score": score,
        "status": status,
        "request": request,
    }


def _inspect(request: dict) -> dict:
    """Inspect a request with the code of the project."""
    answer = _run_helper(request)
    if answer:
        return answer
    return _inspect_in_python(request)


def _last() -> dict:
    """The last inspected request."""
    if "last" not in _STATE:
        _STATE["last"] = _inspect(dict(SAMPLE))
    return _STATE["last"]


def summary() -> dict:
    """The rule set, the findings of the last request and the decision."""
    last = _last()
    rules = last.get("rules", [])
    findings = last.get("findings", [])
    categories = sorted({rule.get("category", "") for rule in rules})
    severities = [rule.get("severity", 0) for rule in rules]
    return {
        "stats": {
            "rules": len(rules),
            "categories": len(categories),
            "max_severity": max(severities) if severities else 0,
            "findings": len(findings),
            "score": last.get("score", 0),
            "decision": last.get("decision", "allow"),
            "status": last.get("status", 200),
        },
        "series": [
            {"label": rule.get("id", ""), "value": rule.get("severity", 0)} for rule in rules
        ],
        "lists": {
            "rules": [
                {
                    "Rule": rule.get("id", ""),
                    "Category": rule.get("category", ""),
                    "Severity": rule.get("severity", 0),
                    "Pattern": rule.get("pattern", ""),
                }
                for rule in rules
            ],
            "findings": [
                {
                    "Rule": finding.get("rule", ""),
                    "Category": finding.get("category", ""),
                    "Severity": finding.get("severity", 0),
                    "Evidence": finding.get("evidence", ""),
                }
                for finding in findings
            ],
        },
    }


def handle(method, path, payload, user):
    """The read and the write endpoints of the firewall."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/rules":
        return 200, {"rules": summary()["lists"]["rules"]}
    if method == "GET" and path == "/api/findings":
        return 200, {"findings": summary()["lists"]["findings"]}
    if method == "POST" and path == "/api/inspect":
        body = payload or {}
        request = {
            "method": str(body.get("method") or "GET").strip() or "GET",
            "path": str(body.get("path") or "/").strip() or "/",
            "query": str(body.get("query") or ""),
            "body": str(body.get("body") or ""),
        }
        answer = _inspect(request)
        _STATE["last"] = answer
        return 200, {
            "decision": answer.get("decision", "allow"),
            "score": answer.get("score", 0),
            "status": answer.get("status", 200),
            "findings": len(answer.get("findings", [])),
        }
    return 404, {"error": "not found"}
