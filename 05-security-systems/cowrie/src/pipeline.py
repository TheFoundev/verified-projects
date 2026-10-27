"""A line-based honeypot server and a small session analysis."""
import json
import socket
import socketserver
import threading
import time
from pathlib import Path

BANNER = "SSH-2.0-OpenSSH_7.4 honeypot\r\n"
PROMPT = b"$ "

#: The commands the honeypot answers, with the files of the fake system.
FILES = {
    "/etc/passwd": "root:x:0:0:root:/root:/bin/sh\n",
    "/etc/hostname": "honeypot\n",
    "/proc/version": "Linux version 4.15.0\n",
}

DIRECTORIES = {
    "/": ["bin", "etc", "home", "root", "tmp", "var"],
    "/etc": ["passwd", "hostname", "shadow"],
    "/root": [".bash_history", ".ssh"],
}


class HoneypotHandler(socketserver.StreamRequestHandler):
    """One connection of the honeypot."""

    # The session is closed after this many seconds without a command.
    idle_timeout = 5.0
    # The session is closed after this many commands.
    max_commands = 25

    def handle(self):
        # The events of every session are collected by the server, so two
        # sessions never write into the same list.
        events = self.server.events
        username = ""
        try:
            self.request.settimeout(self.idle_timeout)
            self.wfile.write(BANNER.encode())
            self.wfile.write(b"login as: ")
            username = self.rfile.readline(128).decode(errors="replace").strip()
            self.wfile.write(b"password: ")
            password = self.rfile.readline(128).decode(errors="replace").strip()
            self._log(events, "login", user=username, password=password)
            self.wfile.write(b"Welcome to Ubuntu 16.04\r\n")

            for _ in range(self.max_commands):
                self.wfile.write(PROMPT)
                line = self.rfile.readline(256).decode(errors="replace").strip()
                if not line:
                    break
                self._log(events, "command", user=username, command=line)
                if line in ("exit", "logout"):
                    break
                self.wfile.write(self.answer(line).encode())
        except (socket.timeout, OSError):
            self._log(events, "timeout", user=username, command="")

    def _log(self, events, kind, **fields):
        events.append({"kind": kind, "source": self.client_address[0], **fields})

    def answer(self, line):
        """The answer of the honeypot to a command."""
        if line == "whoami":
            return "root\n"
        if line == "pwd":
            return "/root\n"
        if line == "uname -a":
            return "Linux honeypot 4.15.0 x86_64 GNU/Linux\n"
        if line == "ls":
            return " ".join(DIRECTORIES["/root"]) + "\n"
        if line.startswith("ls "):
            directory = line[3:].strip() or "/"
            entries = DIRECTORIES.get(directory)
            if entries is None:
                return f"ls: cannot access '{directory}': No such file or directory\n"
            return " ".join(entries) + "\n"
        if line.startswith("cat "):
            path = line[4:].strip()
            content = FILES.get(path)
            if content is None:
                return f"cat: {path}: No such file or directory\n"
            return content
        return f"{line.split()[0]}: command not found\n"


class HoneypotServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The events of every session, with the lock that keeps the sessions
        # from writing into the list at the same time.
        self.events = []
        self.events_lock = threading.Lock()

    def record(self, event):
        with self.events_lock:
            self.events.append(event)

    def events_snapshot(self):
        with self.events_lock:
            return list(self.events)


def simulate_client(port, username, password, commands):
    """A client that talks to the honeypot."""
    with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
        client.settimeout(3)
        client.recv(256)
        client.sendall((username + "\n").encode())
        client.recv(64)
        client.sendall((password + "\n").encode())
        client.recv(128)
        for command in commands:
            client.sendall((command + "\n").encode())
            client.recv(256)
        client.sendall(b"exit\n")


def run(quick=False, base_dir="artifacts"):
    """Start the honeypot, let a few clients in and write the log."""
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)

    server = HoneypotServer(("127.0.0.1", 0), HoneypotHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    attempts = [("admin", "admin", ["whoami", "ls"]),
                ("root", "toor", ["cat /etc/passwd", "uname -a", "exit"])]
    for index in range(1 if quick else 3):
        username, password, commands = attempts[index % len(attempts)]
        try:
            simulate_client(port, f"{username}{index}", password, commands)
        except OSError:
            pass

    server.shutdown()
    server.server_close()
    thread.join(timeout=2)

    events = server.events_snapshot()
    log_path = base / "events.jsonl"
    log_path.write_text(
        "\n".join(json.dumps(event) for event in events) + ("\n" if events else ""),
        encoding="utf-8",
    )
    commands = [event for event in events if event["kind"] == "command"]
    logins = [event for event in events if event["kind"] == "login"]
    return {
        "sessions": len(logins),
        "commands": len(commands),
        "unique_users": len({event["user"] for event in logins}),
        "port": port,
        "log": str(log_path),
    }


def demo(base_dir="artifacts"):
    summary = run(quick=True, base_dir=base_dir)
    events = [
        json.loads(line)
        for line in (Path(base_dir) / "events.jsonl").read_text().splitlines()
        if line
    ]
    print(
        f"Captured {summary['sessions']} session(s) and {summary['commands']} command(s) "
        f"on port {summary['port']}"
    )
    for event in events[:8]:
        print(" ", event)
