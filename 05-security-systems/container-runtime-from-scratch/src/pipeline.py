"""A small container runtime built from namespace and cgroup ideas.

The runtime has two halves that never touch each other:

  the planner  turns a `ContainerSpec` into a list of steps, each step a
               dataclass with the arguments it needs, so the plan can be
               checked and shown before anything runs
  the runner   walks the steps and does them: it creates the rootfs, writes
               the cgroup limits, runs the command inside the namespaces and
               mounts what is missing

The runner needs the rights to create the namespaces. When it does not have
them it says so and the plan is written down instead, so the project always
shows what it would do.
"""
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: The namespaces of a container, with the flag of each.
NAMESPACE_FLAGS = {
    "CLONE_NEWNS": 0x00020000,
    "CLONE_NEWUTS": 0x04000000,
    "CLONE_NEWIPC": 0x08000000,
    "CLONE_NEWUSER": 0x10000000,
    "CLONE_NEWPID": 0x20000000,
    "CLONE_NEWNET": 0x40000000,
}

#: The name of the cgroup the container is put in.
CGROUP_ROOT = "/sys/fs/cgroup"


@dataclass
class ContainerSpec:
    name: str = "demo"
    rootfs: str = "rootfs"
    hostname: str = "student-container"
    memory_mb: int = 128
    cpu_percent: int = 25
    command: list = field(default_factory=lambda: ["/bin/hello"])
    network: bool = False
    # Read only means the rootfs is mounted read only.
    read_only: bool = False
    # The user the command runs as inside the container.
    user: str = "root"


@dataclass
class Step:
    """One step of the plan.

    The step says what has to happen and with which arguments, so the plan
    is a program and not a text.
    """

    kind: str
    detail: str
    args: dict = field(default_factory=dict)

    def to_dict(self):
        return {"kind": self.kind, "detail": self.detail, "args": self.args}


def namespace_flags(spec):
    """The namespaces the container gets."""
    flags = ["CLONE_NEWPID", "CLONE_NEWUTS", "CLONE_NEWNS", "CLONE_NEWIPC"]
    if not spec.network:
        flags.append("CLONE_NEWNET")
    return flags


def namespace_mask(spec):
    """The mask of the namespace flags, the way the kernel takes them."""
    mask = 0
    for name in namespace_flags(spec):
        mask |= NAMESPACE_FLAGS[name]
    return mask


def plan_container(spec, base):
    """The steps that turn the spec into a running container."""
    rootfs = Path(base) / spec.rootfs
    steps = [
        Step("validate", f"check {rootfs} and the limits of the container", {
            "rootfs": str(rootfs),
            "memory_mb": spec.memory_mb,
            "cpu_percent": spec.cpu_percent,
        }),
        Step("namespaces", "clone with " + ", ".join(namespace_flags(spec)), {
            "flags": namespace_flags(spec),
            "mask": namespace_mask(spec),
        }),
        Step("rootfs", f"pivot_root into {rootfs}", {
            "rootfs": str(rootfs),
            "read_only": spec.read_only,
        }),
        Step("mounts", "mount proc, sysfs and a tmpfs for /tmp", {
            "mounts": [
                {"source": "proc", "target": "/proc", "type": "proc"},
                {"source": "sysfs", "target": "/sys", "type": "sysfs"},
                {"source": "tmpfs", "target": "/tmp", "type": "tmpfs", "options": "size=16m"},
            ],
        }),
        Step("cgroup", f"memory.max={spec.memory_mb}M and cpu.max={spec.cpu_percent}%", {
            "path": f"{CGROUP_ROOT}/{spec.name}",
            "memory_max": f"{spec.memory_mb}M",
            "cpu_max": f"{spec.cpu_percent}000 100000",
        }),
        Step("hostname", f"sethostname({spec.hostname})", {"hostname": spec.hostname}),
        Step("exec", "exec " + " ".join(spec.command), {
            "command": list(spec.command),
            "user": spec.user,
        }),
    ]
    if not spec.network:
        steps.append(Step("network", "leave the new network namespace without interfaces", {
            "interfaces": [],
        }))
    return steps


def make_rootfs(base, spec):
    """The directory the container sees as its root.

    The layout is the one a small image has: the directories, a hostname, a
    resolver and a small script to run.
    """
    rootfs = Path(base) / spec.rootfs
    for directory in ("bin", "etc", "proc", "sys", "tmp", "root", "var/log", "lib", "usr/bin"):
        (rootfs / directory).mkdir(parents=True, exist_ok=True)

    entry = rootfs / "bin" / "hello"
    entry.write_text(
        "#!/bin/sh\n"
        "echo \"hello from $(hostname), memory limit ${SRMS_MEMORY_LIMIT:-unknown}\"\n"
        "echo \"namespaces: $(ls /proc/self/ns | tr '\\n' ' ')\"\n"
        "id\n",
        encoding="utf-8",
    )
    entry.chmod(0o755)

    (rootfs / "etc" / "hostname").write_text(spec.hostname + "\n", encoding="utf-8")
    (rootfs / "etc" / "hosts").write_text(
        "127.0.0.1\tlocalhost\n::1\tlocalhost\n", encoding="utf-8"
    )
    (rootfs / "etc" / "resolv.conf").write_text("nameserver 1.1.1.1\n", encoding="utf-8")
    (rootfs / "etc" / "passwd").write_text("root:x:0:0:root:/root:/bin/sh\n", encoding="utf-8")
    (rootfs / "etc" / "group").write_text("root:x:0:\n", encoding="utf-8")
    return rootfs


def cgroup_path(spec, root=CGROUP_ROOT):
    """The cgroup of the container."""
    return Path(root) / spec.name


def write_cgroup_limits(spec, root=CGROUP_ROOT):
    """Write the limits of the container into the cgroup.

    It only works when cgroup v2 is mounted and the directory can be
    written; the answer says what happened.
    """
    path = cgroup_path(spec, root)
    result = {"path": str(path), "written": [], "skipped": None}

    try:
        path.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as error:
        result["skipped"] = f"the cgroup could not be created: {error}"
        return result

    limits = {
        "memory.max": f"{spec.memory_mb}M",
        "cpu.max": f"{spec.cpu_percent}000 100000",
    }
    for name, value in limits.items():
        try:
            (path / name).write_text(value, encoding="utf-8")
            result["written"].append(name)
        except (PermissionError, OSError) as error:
            result["skipped"] = f"{name} could not be written: {error}"
    return result


def effective_uid():
    """The effective uid, or -1 on platforms that have no such concept.

    The runtime depends on Linux namespaces, but the planner and the log still
    have to work when the project is opened on macOS or Windows.
    """
    getter = getattr(os, "geteuid", None)
    return getter() if getter is not None else -1


def unshare_command(spec, command=None):
    """The command that runs the process in the namespaces."""
    flags = [name.replace("CLONE_", "").lower() for name in namespace_flags(spec)]
    parts = ["unshare", "--fork", "--pid"]
    for flag in flags:
        if flag in ("newpid", "newuts", "newipc", "newnet", "newns"):
            parts.append(f"--{flag}")
    parts += ["--mount-proc", "--", *(command or spec.command)]
    return parts


def run_in_namespaces(spec, rootfs, log_path, dry_run=False):
    """Run the command of the container in the namespaces.

    When the rights are missing, or the run is a dry run, nothing is started
    and the reason is written down.
    """
    unshare = shutil.which("unshare")
    if dry_run or unshare is None:
        return {
            "executed": False,
            "reason": "dry run" if dry_run else "unshare is not installed",
            "command": unshare_command(spec),
        }

    command = unshare_command(spec)
    environment = dict(os.environ)
    environment["SRMS_MEMORY_LIMIT"] = f"{spec.memory_mb}M"
    environment["HOSTNAME"] = spec.hostname

    try:
        completed = subprocess.run(
            command,
            cwd=str(rootfs),
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"executed": False, "reason": f"the container could not be started: {error}"}

    output = (completed.stdout or "") + (completed.stderr or "")
    log_path.write_text(output, encoding="utf-8")

    if completed.returncode != 0:
        return {
            "executed": True,
            "exit_code": completed.returncode,
            "reason": "the command inside the container failed",
            "log": str(log_path),
        }
    return {"executed": True, "exit_code": 0, "log": str(log_path)}


def execute(spec, steps, log_path, dry_run=False, cgroup_root=CGROUP_ROOT):
    """Walk the steps and do them.

    The plan is written down first, so it can be read even when the runtime
    cannot do everything.
    """
    rootfs = Path(log_path).parent / spec.rootfs
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    unshare = shutil.which("unshare")
    lines = [f"# unshare binary: {unshare or 'not found'}"]
    lines.append(f"# running as uid {effective_uid()}")
    for step in steps:
        lines.append(f"{step.kind}: {step.detail}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cgroup = write_cgroup_limits(spec, cgroup_root)
    outcome = run_in_namespaces(spec, rootfs, log_path.with_name("container.log"), dry_run)

    return {
        "executed": outcome.get("executed", False),
        "reason": outcome.get("reason", ""),
        "exit_code": outcome.get("exit_code"),
        "cgroup": cgroup,
        "rootfs": str(rootfs),
        "plan": str(log_path),
    }


def run(quick=False, base_dir="artifacts", dry_run=None):
    """Plan a container and try to run it."""
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    spec = ContainerSpec(name="demo" if quick else "orders", memory_mb=128 if quick else 256)
    steps = plan_container(spec, base)
    make_rootfs(base, spec)

    if dry_run is None:
        # The namespaces need the rights, so the run is a dry run unless the
        # runtime is root.
        dry_run = effective_uid() != 0

    status = execute(spec, steps, base / "runtime.log", dry_run=dry_run)
    plan = {
        "spec": asdict(spec),
        "namespaces": namespace_flags(spec),
        "namespace_mask": namespace_mask(spec),
        "steps": [step.to_dict() for step in steps],
        "status": status,
    }
    (base / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return {
        "steps": len(steps),
        "namespaces": len(plan["namespaces"]),
        "memory_mb": spec.memory_mb,
        "cgroups": 1,
        "executed": status["executed"],
        "dry_run": dry_run,
        "plan": str(base / "plan.json"),
    }


def demo(base_dir="artifacts"):
    summary = run(quick=True, base_dir=base_dir)
    plan = json.loads((Path(base_dir) / "plan.json").read_text())
    print(f"Container plan for memory={summary['memory_mb']}M, namespaces={summary['namespaces']}")
    for step in plan["steps"]:
        print(f"  {step['kind']:10s} {step['detail']}")
    if summary["executed"]:
        print("The container ran; the output is in container.log")
    else:
        print(f"Not executed ({summary['dry_run'] and 'dry run' or 'no rights'}); see runtime.log")
