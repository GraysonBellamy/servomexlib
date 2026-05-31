"""Every console script runs hardware-free from a --fixture and exits 0."""

from __future__ import annotations

import csv as csv_module
from pathlib import Path
from typing import TYPE_CHECKING

from servomexlib.cli import capture, discover, read, stream
from servomexlib.cli.diagnostics import main as diag_main

if TYPE_CHECKING:
    import pytest

_FIXTURE = str(
    Path(__file__).parent.parent / "fixtures" / "captures" / "continuous_4100_idle_5ch.bin"
)


def test_read_from_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    rc = read.main(["--fixture", _FIXTURE])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Oxygen" in out


def test_read_no_identify(capsys: pytest.CaptureFixture[str]) -> None:
    rc = read.main(["--fixture", _FIXTURE, "--no-identify"])
    assert rc == 0
    assert "I1" in capsys.readouterr().out


def test_stream_from_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    rc = stream.main(["--fixture", _FIXTURE, "--count", "5"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("\n") >= 5


def test_discover_from_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    rc = discover.main(["--fixture", _FIXTURE])
    out = capsys.readouterr().out
    assert rc == 0
    assert "continuous" in out


def test_capture_to_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_file = tmp_path / "run.csv"
    rc = capture.main(
        ["--fixture", _FIXTURE, "--out", str(out_file), "--rate", "50", "--duration", "0.1"]
    )
    assert rc == 0
    assert "captured" in capsys.readouterr().out
    with out_file.open(newline="") as fh:
        rows = list(csv_module.DictReader(fh))
    assert rows
    assert "channel" in rows[0]


def test_diag_tap_from_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    rc = diag_main(["tap", "--fixture", _FIXTURE, "--count", "5"])
    assert rc == 0
    assert capsys.readouterr().out


def test_diag_jitter_from_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    rc = diag_main(["jitter", "--fixture", _FIXTURE, "--count", "6"])
    assert rc == 0
    assert "samples=" in capsys.readouterr().out
