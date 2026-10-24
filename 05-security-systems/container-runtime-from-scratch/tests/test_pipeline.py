"""Tests for the container runtime."""
import os
import shutil
from pathlib import Path

from src.pipeline import (
    CGROUP_ROOT,
    NAMESPACE_FLAGS,
    ContainerSpec,
    Step,
    execute,
    make_rootfs,
    namespace_flags,
    namespace_mask,
    plan_container,
    run,
    unshare_command,
    write_cgroup_limits,
    demo,
)


def test_pipeline_runs(tmp_path, capsys):
    summary = run(quick=True, base_dir=str(tmp_path))
    assert isinstance(summary, dict)
    assert summary
    assert any(tmp_path.iterdir())

    demo(base_dir=str(tmp_path))
    assert capsys.readouterr().out.strip()


def test_the_namespaces_are_the_ones_a_container_needs():
    spec = ContainerSpec(network=False)
    flags = namespace_flags(spec)
    assert "CLONE_NEWNS" in flags
    assert "CLONE_NEWPID" in flags
    assert "CLONE_NEWNET" in flags


def test_a_container_with_a_network_has_no_new_network_namespace():
    spec = ContainerSpec(network=True)
    assert "CLONE_NEWNET" not in namespace_flags(spec)


def test_the_namespace_mask_is_the_flags_together():
    spec = ContainerSpec(network=False)
    mask = namespace_mask(spec)
    for name in namespace_flags(spec):
        assert mask & NAMESPACE_FLAGS[name]


def test_the_plan_is_a_list_of_steps(tmp_path):
    spec = ContainerSpec()
    steps = plan_container(spec, tmp_path)
    assert steps
    for step in steps:
        assert isinstance(step, Step)
        assert step.kind
        assert step.detail


def test_the_plan_has_the_steps_a_container_needs(tmp_path):
    steps = plan_container(ContainerSpec(), tmp_path)
    kinds = [step.kind for step in steps]
    for wanted in ("validate", "namespaces", "rootfs", "mounts", "cgroup", "hostname", "exec"):
        assert wanted in kinds


def test_the_cgroup_step_carries_the_limits(tmp_path):
    spec = ContainerSpec(memory_mb=256, cpu_percent=30)
    steps = plan_container(spec, tmp_path)
    cgroup = next(step for step in steps if step.kind == "cgroup")
    assert cgroup.args["memory_max"] == "256M"
    assert cgroup.args["cpu_max"] == "30000 100000"


def test_the_rootfs_has_the_layout_of_an_image(tmp_path):
    spec = ContainerSpec()
    rootfs = make_rootfs(tmp_path, spec)
    for directory in ("bin", "etc", "proc", "sys", "tmp"):
        assert (rootfs / directory).is_dir()
    assert (rootfs / "bin" / "hello").exists()
    assert (rootfs / "etc" / "hostname").read_text().strip() == spec.hostname


def test_the_command_names_the_namespaces(tmp_path):
    spec = ContainerSpec()
    command = unshare_command(spec)
    assert command[0] == "unshare"
    assert "--pid" in command
    assert "--mount-proc" in command


def test_the_cgroup_limits_are_written_when_they_can_be(tmp_path):
    spec = ContainerSpec(name="test-container", memory_mb=64, cpu_percent=10)
    result = write_cgroup_limits(spec, root=str(tmp_path))

    if result["written"]:
        assert "memory.max" in result["written"]
        assert (tmp_path / "test-container" / "memory.max").read_text() == "64M"
    else:
        # Without the rights the answer says why.
        assert result["skipped"]


def test_the_run_says_what_it_did(tmp_path):
    summary = run(quick=True, base_dir=str(tmp_path), dry_run=True)
    assert summary["executed"] is False
    assert summary["dry_run"] is True

    plan = (tmp_path / "plan.json").read_text()
    assert "namespaces" in plan
    assert "runtime.log" in plan


def test_the_plan_is_written_down_even_when_the_container_cannot_run(tmp_path):
    summary = run(quick=True, base_dir=str(tmp_path), dry_run=True)
    log = (tmp_path / "runtime.log").read_text()
    assert "unshare binary" in log
    assert "cgroup" in log
    assert summary["plan"].endswith("plan.json")


def test_the_container_really_runs_when_the_rights_are_there(tmp_path):
    """With the rights the command of the container is really started."""
    if getattr(os, "geteuid", lambda: -1)() != 0 or shutil.which("unshare") is None:
        return

    spec = ContainerSpec(name="real", command=["/bin/echo", "hello from the container"])
    make_rootfs(tmp_path, spec)
    steps = plan_container(spec, tmp_path)
    status = execute(spec, steps, tmp_path / "runtime.log", dry_run=False)

    assert status["executed"] is True
    assert status["exit_code"] == 0
