#!/usr/bin/env python3
"""Serve the Escrow & Tokenization project console.

This server has no third-party dependencies. It serves the static site in this
folder and exposes two real endpoints:

  GET  /api/summary  counts the files, lines and tests of this repository and
                     groups the source lines by language
  POST /api/run      executes the project's own demo command and returns its
                     actual stdout, stderr and exit code

Nothing is mocked. If a toolchain or dependency is missing, the output says so.

Usage:  py -3 web/server.py [--port 8000] [--host 127.0.0.1]
"""
import argparse
import json
import os
import shutil
import subprocess
import time
from collections import Counter
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parent
ROOT = WEB.parent
DEMO = ["npm","test"]
DEMO_TIMEOUT = 120
SKIP_DIRS = {".git", "node_modules", "__pycache__", "target", "build", "dist",
             ".venv", "venv", "vendor", ".idea", ".gradle", "artifacts"}
LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".jsx": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++",
    ".cs": "C#", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift",
    ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang", ".hrl": "Erlang",
    ".sol": "Solidity", ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell",
    ".yml": "YAML", ".yaml": "YAML", ".sql": "SQL", ".css": "CSS",
    ".html": "HTML", ".md": "Markdown", ".tf": "Terraform", ".toml": "TOML",
}
TEST_HINTS = ("test", "spec")


def source_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in {"package-lock.json", "Cargo.lock", "rebar.lock"}:
            continue
        yield path


def count_lines(path):
    try:
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def count_tests(files):
    total = 0
    for path in files:
        if not any(hint in path.name.lower() for hint in TEST_HINTS):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        total += text.count("def test_")
        total += text.count("@Test")
        total += text.count("test(") if path.suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx"} else 0
        if path.suffix == ".go":
            total += text.count("func Test")
        if path.suffix == ".rs":
            total += text.count("#[test]")
        if path.suffix in {".c", ".h"}:
            total += text.count("TEST(") + text.count("RUN_TEST(")
    return total


def language_of(path):
    return LANGUAGES.get(path.suffix.lower(), None)


def summary():
    files = [path for path in source_files() if language_of(path)]
    by_language = Counter()
    per_file = []
    for path in files:
        lines = count_lines(path)
        by_language[language_of(path)] += lines
        per_file.append({
            "file": str(path.relative_to(ROOT)).replace(os.sep, "/"),
            "language": language_of(path),
            "lines": lines,
        })
    per_file.sort(key=lambda row: row["lines"], reverse=True)
    tool = DEMO[0]
    available = shutil.which(tool) is not None
    reason = "" if available else "The command '" + tool + "' is not installed on this machine."
    total_lines = sum(by_language.values())
    top_language = by_language.most_common(1)[0][0] if by_language else "—"
    return {
        "name": "Escrow & Tokenization",
        "kind": "a Solidity contract suite",
        "stats": {
            "Files": len(per_file),
            "Lines": f"{total_lines:,}",
            "Tests": count_tests(files),
            "Language": top_language,
        },
        "series": [
            {"label": name, "value": lines}
            for name, lines in by_language.most_common(8)
        ],
        "lists": {
            "largest": per_file[:12],
            "commands": [
                {"command": command, "purpose": purpose}
                for command, purpose in [["npm install","install the Hardhat toolchain"],["npm test","compile and run the contract suite"],["npx hardhat node","start a local chain"]]
            ],
        },
        "demo": {
            "label": "Compile and run the contract suite",
            "command": " ".join(DEMO),
            "available": available,
            "reason": reason,
        },
    }


def run_demo():
    tool = DEMO[0]
    if shutil.which(tool) is None:
        return {
            "command": " ".join(DEMO),
            "exit_code": 127,
            "stdout": "",
            "stderr": "The command '" + tool + "' is not installed on this machine.",
            "seconds": 0,
        }, 200
    started = time.time()
    try:
        completed = subprocess.run(
            DEMO,
            cwd=str(ROOT),
            capture_output=True,
            timeout=DEMO_TIMEOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdout = completed.stdout[-16000:]
        stderr = completed.stderr[-8000:]
        return {
            "command": " ".join(DEMO),
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "seconds": round(time.time() - started, 2),
        }, 200
    except subprocess.TimeoutExpired:
        return {
            "command": " ".join(DEMO),
            "exit_code": 124,
            "stdout": "",
            "stderr": "The command did not finish within " + str(DEMO_TIMEOUT) + " seconds.",
            "seconds": round(time.time() - started, 2),
        }, 200


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def log_message(self, fmt, *args):
        print("  " + fmt % args)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/summary":
            self.send_json(summary())
            return
        if self.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0] == "/api/run":
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            payload, status = run_demo()
            self.send_json(payload, status)
            return
        self.send_json({"error": "not found"}, 404)


def main():
    parser = argparse.ArgumentParser(description="Serve the Escrow & Tokenization project console.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Escrow & Tokenization console on http://{args.host}:{args.port}")
    print("  GET  /api/summary   repository facts")
    print("  POST /api/run       run: " + " ".join(DEMO))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
