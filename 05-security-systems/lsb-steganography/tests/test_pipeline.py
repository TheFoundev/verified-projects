"""Tests for the steganography."""
import numpy as np

from src.pipeline import demo, embed, extract, make_cover, psnr, run, to_bits


def test_pipeline_runs(tmp_path, capsys):
    summary = run(quick=True, base_dir=str(tmp_path))
    assert isinstance(summary, dict)
    assert summary
    assert any(tmp_path.iterdir())

    demo(base_dir=str(tmp_path))
    assert capsys.readouterr().out.strip()


def test_the_payload_becomes_bits():
    bits = to_bits("A")
    assert len(bits) == 40, "the length of the message is in the first four bytes"
    assert all(bit in (0, 1) for bit in bits)


def test_the_message_is_hidden_in_the_cover_and_found_again():
    cover = make_cover(size=48, seed=2)
    stego = embed(cover, "hello")
    assert stego.shape == cover.shape
    assert extract(stego) == "hello"


def test_an_empty_message_is_found_again():
    cover = make_cover(size=48, seed=2)
    stego = embed(cover, "")
    assert extract(stego) == ""


def test_the_cover_and_the_hidden_message_are_close():
    cover = make_cover(size=48, seed=2)
    stego = embed(cover, "hello")
    assert psnr(cover, stego) > 25


def test_another_cover_gives_another_message():
    """A message that was not hidden in the picture is not found in it."""
    other = make_cover(size=48, seed=9)
    assert extract(other) != "hello"
