"""The API of the project: the typosquatting candidates of a domain.

The numbers come from the real engine of the project: the permutations are
built by `permutations()` and every candidate is looked up by `probe()`, the
same way `run()` of the project does it.
"""

from __future__ import annotations

import csv
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline import (  # noqa: E402
    HOMOGLYPHS,
    KEYBOARD,
    TLDS,
    permutations,
    probe,
    run,
    split_domain,
)

#: Where the report of the scan is written.
BASE_DIR = ROOT / "artifacts"
REPORT = BASE_DIR / "candidates.csv"

#: The domain the project scans and the result of the last scan.
_STATE: dict = {}


def scan(domain: str) -> dict:
    """Scan a domain: build the candidates and look every one of them up."""
    candidates = permutations(domain)
    with ThreadPoolExecutor(max_workers=16) as pool:
        addresses = list(pool.map(probe, candidates))
    rows = [
        {"domain": candidate, "resolved": bool(address), "address": address or ""}
        for candidate, address in zip(candidates, addresses)
    ]
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    with REPORT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["domain", "resolved", "address"])
        for row in rows:
            writer.writerow([row["domain"], int(row["resolved"]), row["address"]])
    resolved = [row for row in rows if row["resolved"]]
    return {
        "domain": domain,
        "candidates": len(rows),
        "resolved": len(resolved),
        "unresolved": len(rows) - len(resolved),
        "sample": rows[0]["domain"] if rows else "",
        "report": str(REPORT),
        "rows": rows,
    }


def state() -> dict:
    """Run the scan of the project and keep its result."""
    if "run" not in _STATE:
        result = run(quick=False, base_dir=str(BASE_DIR))
        rows = []
        if REPORT.exists():
            with REPORT.open(encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    rows.append({
                        "domain": row["domain"],
                        "resolved": row["resolved"] == "1",
                        "address": row["address"],
                    })
        result["rows"] = rows
        _STATE["run"] = result
    return _STATE


def families(domain: str) -> list:
    """How many candidates every family of the typos gives.

    The families are the ones the permutations are built from; a candidate
    can belong to more than one of them.
    """
    name, tld = split_domain(domain)
    dropped = {name[:i] + name[i + 1:] + "." + tld for i in range(len(name))}
    doubled = {name[:i] + name[i] * 2 + name[i + 1:] + "." + tld for i in range(len(name))}
    keyboard = {
        name[:i] + KEYBOARD[name[i]] + name[i + 1:] + "." + tld
        for i in range(len(name)) if name[i] in KEYBOARD
    }
    homoglyph = {
        name[:i] + HOMOGLYPHS[name[i]] + name[i + 1:] + "." + tld
        for i in range(len(name)) if name[i] in HOMOGLYPHS
    }
    swapped = {
        name[:i] + name[i + 1] + name[i] + name[i + 2:] + "." + tld
        for i in range(len(name) - 1)
    }
    other = {name + "." + other_tld for other_tld in TLDS}
    return [
        {"family": "a letter left out", "candidates": len(dropped)},
        {"family": "a letter written twice", "candidates": len(doubled)},
        {"family": "the key next door", "candidates": len(keyboard)},
        {"family": "the homoglyphs", "candidates": len(homoglyph)},
        {"family": "two letters swapped", "candidates": len(swapped)},
        {"family": "the other top level domains", "candidates": len(other)},
    ]


def summary() -> dict:
    """The state of the last scan: the candidates and how many resolve."""
    current = state()
    result = current["run"]
    rows = result["rows"]
    return {
        "stats": {
            "candidates": result["candidates"],
            "resolved": result["resolved"],
            "unresolved": result["unresolved"],
            "domain": result["domain"],
        },
        "series": [
            {"label": "resolved", "value": result["resolved"]},
            {"label": "unresolved", "value": result["unresolved"]},
        ],
        "lists": {
            "candidates": [
                {
                    "domain": row["domain"],
                    "resolved": "yes" if row["resolved"] else "no",
                    "address": row["address"] or "-",
                }
                for row in rows
            ],
            "families": families(result["domain"]),
        },
    }


def handle(method, path, payload, user):
    """The read and the write endpoints of the project."""
    if method == "GET" and path == "/api/candidates":
        return 200, {"candidates": summary()["lists"]["candidates"]}
    if method == "GET" and path == "/api/families":
        return 200, {"families": families(state()["run"]["domain"])}
    if method == "POST" and path == "/api/run":
        domain = str((payload or {}).get("domain") or "").strip() or "example.com"
        result = scan(domain)
        _STATE["run"] = result
        return 200, {
            "domain": result["domain"],
            "candidates": result["candidates"],
            "resolved": result["resolved"],
            "unresolved": result["unresolved"],
        }
    return 404, {"error": "not found"}
