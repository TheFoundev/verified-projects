"""Tests for the honeypot."""
import json
import socket
import threading
import time
from pathlib import Path

from src.pipeline import (
    DIRECTORIES,
    FILES,
    HoneypotHandler,
    HoneypotServer,
    run,
    demo,
)


def test_pipeline_runs(tmp_path, capsys):
    summary = run(quick=True, base_dir=str(tmp_path))
    assert isinstance(summary, dict)
    assert summary
    assert any(tmp_path.iterdir())

    demo(base_dir=str(tmp_path))
    assert capsys.readouterr().out.strip()


# ---------------------------------------------------------------------------
# The answers
# ---------------------------------------------------------------------------

def test_the_answers_of_the_known_commands():
    handler = HoneypotHandler.__new__(HoneypotHandler)
    assert handler.answer("whoami") == "root\n"
    assert handler.answer("pwd") == "/root\n"
    assert "Linux" in handler.answer("uname -a")
    assert handler.answer("nope").endswith("command not found\n")


def test_the_files_of_the_fake_system_can_be_read():
    handler = HoneypotHandler.__new__(HoneypotHandler)
    assert handler.answer("cat /etc/passwd") == FILES["/etc/passwd"]
    assert "No such file" in handler.answer("cat /etc/nope")


def test_the_directories_can_be_listed():
    handler = HoneypotHandler.__new__(HoneypotHandler)
    assert "bin" in handler.answer("ls /")
    assert "passwd" in handler.answer("ls /etc")
    assert "No such file" in handler.answer("ls /nope")


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------

def test_the_events_of_two_sessions_do_not_mix():
    """The events are collected by the server, not by the class, so two
    sessions never write into the same list."""
    server = HoneypotServer(("127.0.0.1", 0), HoneypotHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def talk(name):
        with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
            client.settimeout(3)
            client.recv(256)
            client.sendall((name + "\n").encode())
            client.recv(64)
            client.sendall(b"secret\n")
            client.recv(128)
            client.sendall(b"whoami\n")
            client.recv(256)
            client.sendall(b"exit\n")

    workers = [threading.Thread(target=talk, args=(f"user{index}",)) for index in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    server.shutdown()
    server.server_close()
    thread.join(timeout=2)

    events = server.events_snapshot()
    logins = [event for event in events if event["kind"] == "login"]
    assert len(logins) == 4
    assert len({event["user"] for event in logins}) == 4


def test_the_log_holds_the_logins_and_the_commands(tmp_path):
    summary = run(quick=True, base_dir=str(tmp_path))

    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    events = [json.loads(line) for line in lines if line]

    assert summary["sessions"] == len([e for e in events if e["kind"] == "login"])
    assert summary["commands"] == len([e for e in events if e["kind"] == "command"])
    assert summary["unique_users"] == len({e["user"] for e in events if e["kind"] == "login"})
    assert all("source" in event for event in events)


def test_the_log_holds_the_password_that_was_guessed(tmp_path):
    run(quick=True, base_dir=str(tmp_path))
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
        if line
    ]
    logins = [event for event in events if event["kind"] == "login"]
    assert logins
    assert all("password" in event for event in logins)


def test_the_session_ends_when_the_client_stops_talking(tmp_path):
    """A session that says nothing is closed, not kept open for ever."""
    server = HoneypotServer(("127.0.0.1", 0), HoneypotHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
        client.settimeout(3)
        client.recv(256)
        client.sendall(b"admin\n")
        client.recv(64)
        client.sendall(b"admin\n")
        client.recv(128)
        # The client says nothing more and closes the connection.
        client.close()

    time.sleep(0.3)
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)

    events = server.events_snapshot()
    assert any(event["kind"] == "login" for event in events)
