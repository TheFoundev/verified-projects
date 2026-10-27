"""The API of the port scanner: the ports of a spec and the state of every port.

The numbers come from the Go code of the project. A helper program is built
from the sources of the project (scanner.go, main.go and go.mod) with a
`main` that reads the ports with ParsePorts, the hosts with ParseHosts and
scans every host with Scan, which dials each port with net.DialTimeout. The
helper prints the answer as JSON.

When the Go toolchain is not installed, the same code is mirrored here:
ParsePorts and ParseHosts are read as they are written in scanner.go and the
ports are dialled with socket.create_connection and the same timeout, so a
port that accepts the connection is open and every other port is closed.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import shutil
import socket
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(tempfile.gettempdir()) / "foundev-go-helpers"
GO = shutil.which("go")

#: How long an answer of the helper is reused.
TTL = 30.0

#: The host and the ports the command line uses, as main.go sets them.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORTS = "22,80,443,8000-8010"

#: The timeout of a port, in milliseconds: DefaultTimeout of scanner.go.
DEFAULT_TIMEOUT_MS = 400

#: The helper program: it parses the spec and scans the hosts.
HELPER = r'''
package main

import (
	"encoding/json"
	"os"
	"time"
)

func main() {
	var input struct {
		Host      string `json:"host"`
		Ports     string `json:"ports"`
		TimeoutMS int    `json:"timeout_ms"`
	}
	if err := json.NewDecoder(os.Stdin).Decode(&input); err != nil {
		os.Exit(1)
	}

	ports, err := ParsePorts(input.Ports)
	if err != nil {
		report(map[string]any{"error": err.Error()})
		return
	}
	hosts, err := ParseHosts(input.Host)
	if err != nil {
		report(map[string]any{"error": err.Error()})
		return
	}

	timeout := time.Duration(input.TimeoutMS) * time.Millisecond
	if timeout <= 0 {
		timeout = DefaultTimeout
	}

	scans := []map[string]any{}
	open := []map[string]any{}
	closed := 0
	for _, host := range hosts {
		results := Scan(host, ports, timeout)
		scans = append(scans, map[string]any{"host": host, "results": results})
		for _, result := range results {
			if result.State == "open" {
				open = append(open, map[string]any{"host": host, "port": result.Port})
			} else {
				closed++
			}
		}
	}

	report(map[string]any{
		"ports":      ports,
		"hosts":      hosts,
		"scans":      scans,
		"open":       open,
		"closed":     closed,
		"timeout_ms": int(timeout / time.Millisecond),
	})
}

func report(answer map[string]any) {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(answer)
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
    target = CACHE / f"naabu-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-naabu-"))
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


def _run_helper(host: str, ports: str, timeout_ms: int) -> dict | None:
    """Ask the code of the project for the state of the ports."""
    binary = _build_helper()
    if binary is None:
        return None
    payload = {"host": host, "ports": ports, "timeout_ms": timeout_ms}
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

def _parse_ports(spec: str) -> list:
    """ParsePorts of scanner.go, mirrored."""
    seen = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            bounds = part.split("-", 1)
            try:
                start = int(bounds[0].strip())
                end = int(bounds[1].strip())
            except ValueError:
                raise ValueError(f'invalid port range "{part}"') from None
            if end < start:
                raise ValueError(f"invalid port range {part!r}")
            for port in range(start, end + 1):
                if 1 <= port <= 65535:
                    seen[port] = True
            continue
        try:
            port = int(part)
        except ValueError:
            raise ValueError(f'invalid port "{part}"') from None
        if port < 1 or port > 65535:
            raise ValueError(f"invalid port {part!r}")
        seen[port] = True
    return sorted(seen)


def _expand_network(spec: str) -> list:
    """expandNetwork of scanner.go, mirrored."""
    parts = spec.split("/")
    if len(parts) != 2:
        raise ValueError(f'"{spec}" is not a network')
    try:
        address = ipaddress.IPv4Address(parts[0])
    except ValueError:
        raise ValueError(f'"{parts[0]}" is not an address') from None
    try:
        bits = int(parts[1])
    except ValueError:
        raise ValueError(f'"{parts[1]}" is not a prefix') from None
    if bits < 0 or bits > 32:
        raise ValueError(f'"{parts[1]}" is not a prefix')
    network = ipaddress.IPv4Network(f"{address}/{bits}", strict=False)
    hosts = [str(current) for current in network]
    if len(hosts) > 65536:
        raise ValueError("the network is too big")
    return hosts


def _parse_hosts(spec: str) -> list:
    """ParseHosts of scanner.go, mirrored."""
    hosts = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" not in part:
            hosts.append(part)
            continue
        hosts.extend(_expand_network(part))
    if not hosts:
        raise ValueError("no hosts to scan")
    return hosts


def _dial(host: str, port: int, timeout_ms: int) -> str:
    """One net.DialTimeout of Scan, mirrored."""
    try:
        with socket.create_connection((host, port), timeout=timeout_ms / 1000.0):
            return "open"
    except Exception:
        return "closed"


def _scan(host: str, ports: list, timeout_ms: int) -> list:
    """Scan of scanner.go, mirrored: every port is dialled at the same time."""
    if not ports:
        return []
    with ThreadPoolExecutor(max_workers=min(len(ports), 64)) as pool:
        states = list(pool.map(lambda port: _dial(host, port, timeout_ms), ports))
    return [
        {"port": port, "state": state}
        for port, state in zip(ports, states)
    ]


def _fallback(host: str, ports_spec: str, timeout_ms: int) -> dict:
    """The answer of the helper, computed in Python."""
    try:
        ports = _parse_ports(ports_spec)
    except ValueError as error:
        return {"error": str(error)}
    try:
        hosts = _parse_hosts(host)
    except ValueError as error:
        return {"error": str(error)}
    if timeout_ms <= 0:
        timeout_ms = DEFAULT_TIMEOUT_MS
    scans, opened, closed = [], [], 0
    for name in hosts:
        results = _scan(name, ports, timeout_ms)
        scans.append({"host": name, "results": results})
        for result in results:
            if result["state"] == "open":
                opened.append({"host": name, "port": result["port"]})
            else:
                closed += 1
    return {
        "ports": ports, "hosts": hosts, "scans": scans, "open": opened,
        "closed": closed, "timeout_ms": timeout_ms,
    }


# --------------------------------------------------------------------------
# the state of the console
# --------------------------------------------------------------------------

_ANSWER: dict = {"at": 0.0, "key": None, "data": None}


def _answer(host: str = DEFAULT_HOST, ports: str = DEFAULT_PORTS,
            timeout_ms: int = DEFAULT_TIMEOUT_MS, force: bool = False) -> dict:
    """The state of the ports, computed by the code of the project."""
    key = f"{host}|{ports}|{timeout_ms}"
    moment = time.time()
    if (not force and _ANSWER["data"] is not None and _ANSWER["key"] == key
            and moment - _ANSWER["at"] < TTL):
        return _ANSWER["data"]
    answer = _run_helper(host, ports, timeout_ms) or _fallback(host, ports, timeout_ms)
    _ANSWER.update({"at": moment, "key": key, "data": answer})
    return answer


# --------------------------------------------------------------------------
# the rows of the console
# --------------------------------------------------------------------------

def _result_rows(answer: dict) -> list:
    """Every port of every host, as the console shows it."""
    rows = []
    for scan in answer.get("scans", []):
        for result in scan.get("results", []):
            rows.append({
                "Host": scan.get("host", ""),
                "Port": result.get("port", 0),
                "State": result.get("state", ""),
            })
    return rows


def _port_rows(answer: dict) -> list:
    """The ports of the first host of the scan."""
    scans = answer.get("scans", [])
    if not scans:
        return []
    host = scans[0].get("host", "")
    return [
        {"Port": result.get("port", 0), "State": result.get("state", ""), "Host": host}
        for result in scans[0].get("results", [])
    ]


def _host_rows(answer: dict) -> list:
    """The hosts of the scan with the number of open and closed ports."""
    rows = []
    for scan in answer.get("scans", []):
        results = scan.get("results", [])
        rows.append({
            "Host": scan.get("host", ""),
            "Open": sum(1 for result in results if result.get("state") == "open"),
            "Closed": sum(1 for result in results if result.get("state") == "closed"),
        })
    return rows


def _open_rows(answer: dict) -> list:
    """The ports that accepted the connection."""
    return [
        {"Host": row.get("host", ""), "Port": row.get("port", 0)}
        for row in answer.get("open", [])
    ]


def summary() -> dict:
    """The ports of the spec and the state of every scanned port."""
    answer = _answer()
    results = _result_rows(answer)
    return {
        "stats": {
            "ports": len(answer.get("ports", [])),
            "hosts": len(answer.get("hosts", [])),
            "open": sum(1 for row in results if row["State"] == "open"),
            "closed": sum(1 for row in results if row["State"] == "closed"),
            "timeout_ms": answer.get("timeout_ms", DEFAULT_TIMEOUT_MS),
        },
        "series": [
            {"label": row.get("Host", ""), "value": row.get("Open", 0)}
            for row in _host_rows(answer)
        ],
        "lists": {
            "results": results,
            "ports": _port_rows(answer),
            "hosts": _host_rows(answer),
            "open": _open_rows(answer),
        },
    }


# --------------------------------------------------------------------------
# the endpoints
# --------------------------------------------------------------------------

def handle(method, path, payload, user):
    """The endpoints of the port scanner."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/results":
        return 200, {"results": _result_rows(_answer())}
    if method == "GET" and path == "/api/ports":
        return 200, {"ports": _port_rows(_answer())}
    if method == "GET" and path == "/api/hosts":
        return 200, {"hosts": _host_rows(_answer())}
    if method == "GET" and path == "/api/open":
        return 200, {"open": _open_rows(_answer())}
    if method == "POST" and path == "/api/scan":
        body = payload or {}
        host = str(body.get("host") or DEFAULT_HOST).strip() or DEFAULT_HOST
        ports = str(body.get("ports") or DEFAULT_PORTS).strip() or DEFAULT_PORTS
        try:
            timeout_ms = int(str(body.get("timeout_ms") or DEFAULT_TIMEOUT_MS).strip())
        except ValueError:
            return 400, {"error": "the timeout is not a number of milliseconds"}
        answer = _answer(host, ports, timeout_ms, force=True)
        if answer.get("error"):
            return 400, {"error": answer["error"]}
        return 200, {
            "host": host,
            "ports": len(answer.get("ports", [])),
            "hosts": len(answer.get("hosts", [])),
            "open": len(answer.get("open", [])),
            "closed": answer.get("closed", 0),
        }
    return 404, {"error": "not found"}
