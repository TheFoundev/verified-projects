"""Git helpers: tracked-file discovery and actor attribution for the audit trail."""

from __future__ import annotations

import getpass
import os
import socket
import subprocess
from pathlib import Path

__all__ = ["git", "is_git_repo", "tracked_files", "walk_files", "actor_for"]


def git(args: list[str], cwd: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def is_git_repo(path: Path) -> bool:
    out = git(["rev-parse", "--is-inside-work-tree"], path)
    return bool(out and out.strip() == "true")


def tracked_files(root: Path) -> list[str] | None:
    """Tracked file list (relative, POSIX), or None outside a git repo."""
    out = git(["ls-files", "-z"], root)
    if out is None:
        return None
    return [f for f in out.split("\0") if f]


def walk_files(root: Path) -> list[str]:
    """Filesystem fallback used outside a git repository."""
    results: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/"):
            continue
        results.append(rel)
    return results


def _ci_actor(env_map: dict) -> str | None:
    if env_map.get("GITHUB_ACTIONS"):
        actor = env_map.get("GITHUB_ACTOR") or "?"
        run = env_map.get("GITHUB_RUN_ID") or "?"
        return f"ci:github:{actor}:{run}"
    if env_map.get("GITLAB_CI"):
        return f"ci:gitlab:{env_map.get('GITLAB_CI_USER') or env_map.get('CI_JOB_ID') or '?'}"
    if env_map.get("JENKINS_URL"):
        return f"ci:jenkins:{env_map.get('BUILD_TAG') or env_map.get('BUILD_NUMBER') or '?'}"
    if env_map.get("BUILD_BUILDID") or env_map.get("TF_BUILD"):
        return f"ci:azure:{env_map.get('BUILD_BUILDID') or '?'}"
    return None


def actor_for(root: Path, env_map: dict | None = None) -> str:
    """Best-effort identity of whoever is running the command.

    Order: explicit ``SECRETSYNC_ACTOR``, CI identity (run id / actor from
    the environment), then the git user, then the local user.
    """
    env_map = dict(os.environ if env_map is None else env_map)
    explicit = env_map.get("SECRETSYNC_ACTOR")
    if explicit:
        return f"override:{explicit}"
    ci = _ci_actor(env_map)
    if ci:
        return ci
    name = git(["config", "user.name"], root)
    email = git(["config", "user.email"], root)
    if name and name.strip():
        if email and email.strip():
            return f"git:{name.strip()} <{email.strip()}>"
        return f"git:{name.strip()}"
    try:
        user = getpass.getuser()
    except Exception:  # pragma: no cover - exotic environments
        user = "unknown"
    return f"local:{user}@{socket.gethostname()}"
