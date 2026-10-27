# Cowrie

A tiny SSH-style honeypot that records login attempts and commands.

> A medium-interaction SSH and Telnet honeypot that records attacker behaviour for defensive research.

## Overview

An original, self-contained implementation of the **Cowrie** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- A honeypot that answers a line-based shell: the files of a small fake
  system can be read and the directories listed
- The events of every session are collected by the server with a lock, so two
  sessions never write into the same list
- A session that says nothing is closed instead of being kept open
- The log of every session: the user, the password and every command

## Stack

`Python`
The CSV stack is Python. Twisted is not installed, so the server uses the standard library socket server and the project runs directly on the host.

## Run

```bash
pip install -r requirements.txt
python main.py run
python main.py demo
pytest -q
```

## Structure

```text
cowrie/
├── main.py                 pipeline CLI
├── src/pipeline.py         data pipeline
├── tests/test_pipeline.py  smoke test
├── requirements.txt
```

## Testing

```bash
```bash
pytest -q
```

8 tests: the answers of the honeypot, the files and the directories of the
fake system, that the events of two sessions do not mix, that the log holds
the logins, the passwords and the commands, and that a session ends when the
client stops talking.
```

## Notes and limitations

- The protocol is a fake line-based shell, not real SSH, so it cannot be logged into with an SSH client.
- Only localhost is used in the demo; there is no container sandbox.
- The log is plain JSONL with no rotation or alerting.

## License

BSD-3-Clause. See `LICENSE`.

## About the scope

This is a third-year student project, not a production data platform. It uses local files and SQLite so the pipeline can run on one machine. The notes above list the production pieces that were replaced or skipped.
