"""Tests for the permutations and the probe."""
import socket

from src.pipeline import (
    HOMOGLYPHS,
    KEYBOARD,
    permutations,
    probe,
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


def test_a_letter_is_left_out():
    assert "gogle.com" in permutations("google.com")


def test_a_letter_is_written_twice():
    assert "gooogle.com" in permutations("google.com")


def test_the_keyboard_is_used():
    assert "gopgle.com" in permutations("google.com")


def test_the_homoglyphs_are_used():
    assert "g0ogle.com" in permutations("google.com")


def test_two_letters_are_swapped():
    assert "ogogle.com" in permutations("google.com")


def test_the_domain_itself_is_not_a_candidate():
    assert "google.com" not in permutations("google.com")


def test_the_other_top_level_domains_are_tried():
    assert "google.io" in permutations("google.com")


def test_the_candidates_are_unique():
    candidates = permutations("example.com")
    assert len(candidates) == len(set(candidates))


def test_the_probe_does_not_change_the_timeout_of_the_process():
    """The timeout is set for the call, not for the whole process."""
    before = socket.getdefaulttimeout()
    probe("this-host-does-not-exist.invalid")
    assert socket.getdefaulttimeout() == before


def test_the_probe_of_a_host_that_does_not_exist_is_none():
    assert probe("this-host-does-not-exist.invalid") is None


def test_the_report_holds_the_candidates(tmp_path):
    summary = run(quick=True, base_dir=str(tmp_path))
    assert summary["candidates"] > 0

    lines = (tmp_path / "candidates.csv").read_text().splitlines()
    assert lines[0].startswith("domain,")
    assert len(lines) - 1 == summary["candidates"]
