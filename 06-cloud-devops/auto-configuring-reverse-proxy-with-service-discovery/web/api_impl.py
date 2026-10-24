"""The API of the proxy: the services of the registry and the health of the instances.

The numbers come from the Go code of the project. A small helper program is
built from the sources of the project (proxy.go and go.mod) with a `main` that
calls ParseConfig and Registry.Pick and prints the result as JSON. When the Go
toolchain is not installed, the same registry is read from the example
configuration that is written in proxy.go, with the same parser.
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

#: The helper program: it calls the code of the project and prints JSON.
HELPER = r'''
package main

import (
	"encoding/json"
	"os"
)

func main() {
	data := []byte(exampleConfig)
	if len(os.Args) > 1 && os.Args[1] != "-" {
		loaded, err := os.ReadFile(os.Args[1])
		if err != nil {
			json.NewEncoder(os.Stdout).Encode(map[string]any{"error": err.Error()})
			os.Exit(1)
		}
		data = loaded
	}

	registry, err := ParseConfig(string(data))
	if err != nil {
		json.NewEncoder(os.Stdout).Encode(map[string]any{"error": err.Error()})
		os.Exit(1)
	}

	services := []map[string]any{}
	instances := []map[string]any{}
	picks := []map[string]any{}
	order := 0
	for _, name := range registry.Names() {
		service := registry.services[name]
		healthy := 0
		for _, instance := range service.Instances {
			if instance.Healthy {
				healthy++
			}
			instances = append(instances, map[string]any{
				"service": service.Name,
				"url":     instance.URL,
				"healthy": instance.Healthy,
			})
		}
		services = append(services, map[string]any{
			"name":      service.Name,
			"instances": len(service.Instances),
			"healthy":   healthy,
		})
		// four picks, so the round-robin order is visible
		for i := 0; i < 4; i++ {
			instance, err := registry.Pick(service.Name)
			if err != nil {
				continue
			}
			order++
			picks = append(picks, map[string]any{
				"order":    order,
				"service":  service.Name,
				"instance": instance.URL,
			})
		}
	}

	json.NewEncoder(os.Stdout).Encode(map[string]any{
		"services":  services,
		"instances": instances,
		"picks":     picks,
	})
}
'''

#: The registry and the health marks the console has set.
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
    target = CACHE / f"proxy-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-proxy-"))
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


def _run_helper() -> dict | None:
    """Run the helper and read its JSON answer."""
    binary = _build_helper()
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=120,
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


def _example_config() -> str:
    """The example configuration of the project, as it is written in proxy.go."""
    text = (ROOT / "proxy.go").read_text(encoding="utf-8")
    match = re.search(r"const exampleConfig = `(.*?)`", text, re.S)
    return match.group(1) if match else ""


def _parse_config(text: str) -> list:
    """The same parser as ParseConfig in proxy.go."""
    services = []
    current = None
    for line in text.split("\n"):
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#") or trimmed == "services:":
            continue
        if trimmed.startswith("- name:"):
            current = {"name": trimmed[len("- name:"):].strip(), "instances": []}
            services.append(current)
        elif trimmed.startswith("- http") or trimmed.startswith("- https"):
            if current is None:
                raise ValueError("instance before any service")
            current["instances"].append(trimmed[2:].strip())
    if not services:
        raise ValueError("no services found")
    return services


def _registry_from_source() -> dict:
    """The registry of the example configuration, read without Go."""
    services = _parse_config(_example_config())
    out_services = []
    out_instances = []
    picks = []
    order = 0
    for service in services:
        out_services.append(
            {"name": service["name"], "instances": len(service["instances"]), "healthy": len(service["instances"])}
        )
        for url in service["instances"]:
            out_instances.append({"service": service["name"], "url": url, "healthy": True})
        for _ in range(4):
            if not service["instances"]:
                continue
            order += 1
            picks.append(
                {
                    "order": order,
                    "service": service["name"],
                    "instance": service["instances"][(order - 1) % len(service["instances"])],
                }
            )
    return {"services": out_services, "instances": out_instances, "picks": picks}


def _registry() -> dict:
    """The registry of the proxy."""
    if "registry" not in _STATE:
        answer = _run_helper()
        _STATE["registry"] = answer if answer else _registry_from_source()
    return _STATE["registry"]


def _is_healthy(instance: dict) -> bool:
    """The health of an instance, with the marks of the console on top."""
    marks = _STATE.setdefault("health", {})
    if instance["url"] in marks:
        return bool(marks[instance["url"]])
    return bool(instance.get("healthy", True))


def _services() -> list:
    """The services of the registry with their instance counts."""
    registry = _registry()
    rows = []
    for service in registry.get("services", []):
        instances = [i for i in registry.get("instances", []) if i["service"] == service["name"]]
        rows.append(
            {
                "name": service["name"],
                "instances": len(instances),
                "healthy": sum(1 for i in instances if _is_healthy(i)),
            }
        )
    return rows


def _instances() -> list:
    """Every instance of every service, with its health."""
    registry = _registry()
    rows = []
    for instance in registry.get("instances", []):
        rows.append(
            {
                "service": instance["service"],
                "url": instance["url"],
                "healthy": _is_healthy(instance),
            }
        )
    return rows


def _picks() -> list:
    """The order of the round robin.

    While every instance is healthy this is the answer of Registry.Pick from
    the helper. As soon as an instance is marked unhealthy, the same rule is
    applied here: the counter goes up once per pick and the first healthy
    instance from there is taken.
    """
    registry = _registry()
    marks = _STATE.setdefault("health", {})
    if not any(value is False for value in marks.values()):
        return registry.get("picks", [])
    picks = []
    order = 0
    for service in registry.get("services", []):
        urls = [i["url"] for i in registry.get("instances", []) if i["service"] == service["name"]]
        if not urls:
            continue
        for attempt in range(4):
            start = attempt % len(urls)
            chosen = None
            for step in range(len(urls)):
                candidate = urls[(start + step) % len(urls)]
                if _is_healthy({"url": candidate, "healthy": True}):
                    chosen = candidate
                    break
            if chosen is None:
                continue
            order += 1
            picks.append({"order": order, "service": service["name"], "instance": chosen})
    return picks


def summary() -> dict:
    """The state of the registry: the services, the instances and the health."""
    services = _services()
    instances = _instances()
    picks = _picks()
    healthy = sum(1 for instance in instances if instance["healthy"])
    return {
        "stats": {
            "services": len(services),
            "instances": len(instances),
            "healthy": healthy,
            "unhealthy": len(instances) - healthy,
        },
        "series": [
            {"label": service["name"], "value": service["instances"]} for service in services
        ],
        "lists": {
            "services": [
                {
                    "Service": service["name"],
                    "Instances": service["instances"],
                    "Healthy": service["healthy"],
                }
                for service in services
            ],
            "instances": [
                {
                    "Service": instance["service"],
                    "Instance": instance["url"],
                    "Healthy": "yes" if instance["healthy"] else "no",
                }
                for instance in instances
            ],
            "picks": [
                {
                    "#": pick["order"],
                    "Service": pick["service"],
                    "Instance": pick["instance"],
                }
                for pick in picks
            ],
        },
    }


def handle(method, path, payload, user):
    """The read and the write endpoints of the proxy."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/services":
        return 200, {"services": summary()["lists"]["services"]}
    if method == "GET" and path == "/api/instances":
        return 200, {"instances": summary()["lists"]["instances"]}
    if method == "POST" and path == "/api/health":
        body = payload or {}
        service = str(body.get("service") or "").strip()
        instance = str(body.get("instance") or "").strip()
        healthy = body.get("healthy", True)
        if isinstance(healthy, str):
            healthy = healthy.strip().lower() not in ("false", "no", "0", "")
        names = [row["name"] for row in _services()]
        if service not in names:
            return 400, {"error": f'unknown service "{service}"'}
        urls = [row["url"] for row in _instances() if row["service"] == service]
        if instance not in urls:
            return 400, {"error": f'unknown instance "{instance}"'}
        _STATE.setdefault("health", {})[instance] = bool(healthy)
        return 200, {"service": service, "instance": instance, "healthy": bool(healthy)}
    return 404, {"error": "not found"}
