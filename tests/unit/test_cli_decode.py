"""servomex-decode CLI: fixture decode, hex decode, bad-checksum reporting."""

from __future__ import annotations

from typing import TYPE_CHECKING

from servomexlib.cli.decode import decode_capture, main
from tests.conftest import CONTINUOUS_FIXTURE

if TYPE_CHECKING:
    import pytest


def test_decode_fixture_exits_zero_and_prints_channels(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--fixture", str(CONTINUOUS_FIXTURE)])
    assert code == 0
    out = capsys.readouterr().out
    assert "I1" in out
    assert "Oxygen" in out
    assert "20.376" in out
    assert "checksum: valid" in out
    assert out.count("channels (5)") == 6  # six frames in the capture


def test_decode_hex_single_frame(
    continuous_frames: list[bytes], capsys: pytest.CaptureFixture[str]
) -> None:
    hex_str = continuous_frames[0].hex(" ")
    code = main(["--hex", hex_str])
    assert code == 0
    assert "Oxygen" in capsys.readouterr().out


def test_decode_reports_invalid_checksum(continuous_frames: list[bytes]) -> None:
    frame = bytearray(continuous_frames[0])
    frame[frame.index(b"Oxygen")] ^= 0x01
    report = decode_capture(bytes(frame))
    assert "INVALID" in report
    assert "parse error" in report


def test_bad_hex_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--hex", "zz"])
    assert code == 2
    assert "error" in capsys.readouterr().err
