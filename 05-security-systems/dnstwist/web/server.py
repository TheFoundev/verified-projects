#!/usr/bin/env python3
"""The web server of the project: it serves the site and the JSON API.

    python3 web/server.py [--port 8000]

The routes are:

    GET  /                       the home page
    GET  /console.html           the console
    GET  /login.html             the sign-in portal
    GET  /assets/*               the theme and the runtime
    GET  /api/summary            the state of the instance (stats, series, lists)
    POST /api/session            sign in
    DELETE /api/session          sign out
    GET  /api/<resource>         the read endpoints of the domain
    POST /api/<resource>         the write endpoints of the domain

The data lives in `web/app.db` (SQLite). The session is a signed, http-only
cookie; the password of the demo account is stored as a salted PBKDF2 hash.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import sqlite3
import sys
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

try:  # the domain logic of the project
    from web.api_impl import handle, summary
except Exception:  # pragma: no cover - the project has no domain module yet
    def summary() -> dict:
        return {"stats": {"status": "running"}, "series": [], "lists": {}}

    def handle(method, path, payload, user):
        return 404, {"error": "not found"}

DB_PATH = HERE / "app.db"
SECRET_PATH = HERE / ".session-secret"
SESSION_COOKIE = "foundev_session"
SESSION_TTL = 8 * 3600
DEMO_USER = "demo"
DEMO_PASSWORD = "demo123"

SECRET = b""


# --------------------------------------------------------------------------
# the database
# --------------------------------------------------------------------------

def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"pbkdf2$120000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def init_db() -> None:
    connection = db()
    connection.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT, created_at REAL)"
    )
    row = connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    if row["n"] == 0:
        connection.execute(
            "INSERT INTO users (username, password, role, created_at) VALUES (?,?,?,?)",
            (DEMO_USER, hash_password(DEMO_PASSWORD), "admin", time.time()),
        )
        connection.commit()
    connection.close()


# --------------------------------------------------------------------------
# the session
# --------------------------------------------------------------------------

def load_secret() -> bytes:
    global SECRET
    if SECRET:
        return SECRET
    if SECRET_PATH.exists():
        SECRET = SECRET_PATH.read_bytes()
    else:
        SECRET = secrets.token_bytes(32)
        SECRET_PATH.write_bytes(SECRET)
        os.chmod(SECRET_PATH, 0o600)
    return SECRET


def sign(value: str) -> str:
    payload = f"{value}.{int(time.time()) + SESSION_TTL}".encode()
    mac = hmac.new(load_secret(), payload, hashlib.sha256).hexdigest()[:32]
    return f"{value}.{payload.decode().split('.', 1)[1]}.{mac}"


def verify(token: str) -> str | None:
    try:
        value, expiry, mac = token.split(".")
        payload = f"{value}.{expiry}".encode()
        expected = hmac.new(load_secret(), payload, hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(mac, expected):
            return None
        if int(expiry) < time.time():
            return None
        return value
    except Exception:
        return None


def current_user(cookie_header: str | None) -> dict | None:
    if not cookie_header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:
        return None
    morsel = jar.get(SESSION_COOKIE)
    if not morsel:
        return None
    username = verify(morsel.value)
    if not username:
        return None
    connection = db()
    row = connection.execute(
        "SELECT id, username, role FROM users WHERE username = ?", (username,)
    ).fetchone()
    connection.close()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# the server
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "FoundevWeb/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the console readable
        sys.stderr.write("  %s\n" % (fmt % args))

    # -- helpers -------------------------------------------------------
    def send_json(self, payload, status=200, cookie: str | None = None):
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, content_type: str | None = None):
        if not path.exists() or not path.is_file():
            self.send_json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        guessed = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", guessed)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode())
        except Exception:
            return {}

    def user(self):
        return current_user(self.headers.get("Cookie"))

    # -- routes --------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        if route.startswith("/api/"):
            return self.api_get(route, parse_qs(parsed.query))
        if route in ("/", "/index.html"):
            return self.send_file(HERE / "index.html", "text/html; charset=utf-8")
        if route == "/console.html":
            return self.send_file(HERE / "console.html", "text/html; charset=utf-8")
        if route == "/login.html":
            return self.send_file(HERE / "login.html", "text/html; charset=utf-8")
        if route.startswith("/assets/"):
            return self.send_file(HERE / route.lstrip("/"))
        if route == "/healthz":
            return self.send_json({"status": "ok"})
        return self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        route = parsed.path
        payload = self.read_body()
        if route == "/api/session":
            return self.sign_in(payload)
        if route.startswith("/api/"):
            try:
                status, data = handle("POST", route, payload, self.user())
            except Exception as error:
                return self.send_json({"error": str(error)}, 500)
            return self.send_json(data, status)
        return self.send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        route = urlparse(self.path).path
        if route == "/api/session":
            cookie = f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
            return self.send_json({"ok": True}, 200, cookie)
        if route.startswith("/api/"):
            try:
                status, data = handle("DELETE", route, {}, self.user())
            except Exception as error:
                return self.send_json({"error": str(error)}, 500)
            return self.send_json(data, status)
        return self.send_json({"error": "not found"}, 404)

    def api_get(self, route: str, query: dict):
        if route == "/api/summary":
            try:
                return self.send_json(summary())
            except Exception as error:
                return self.send_json({"error": str(error), "stats": {}, "series": [], "lists": {}}, 200)
        try:
            status, data = handle("GET", route, query, self.user())
        except Exception as error:
            return self.send_json({"error": str(error)}, 500)
        return self.send_json(data, status)

    def sign_in(self, payload: dict):
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        connection = db()
        row = connection.execute(
            "SELECT id, username, password, role FROM users WHERE username = ?", (username,)
        ).fetchone()
        connection.close()
        if not row or not verify_password(password, row["password"]):
            return self.send_json({"error": "Wrong user name or password."}, 401)
        cookie = (
            f"{SESSION_COOKIE}={sign(row['username'])}; Path=/; Max-Age={SESSION_TTL}; "
            "HttpOnly; SameSite=Lax"
        )
        return self.send_json(
            {"user": {"name": row["username"], "role": row["role"]}}, 200, cookie
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--host", default="127.0.0.1")
    arguments = parser.parse_args()

    init_db()
    load_secret()
    server = ThreadingHTTPServer((arguments.host, arguments.port), Handler)
    print(f"{Path(ROOT).name} is served on http://{arguments.host}:{arguments.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
