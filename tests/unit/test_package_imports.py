"""Smoke test: the package imports and exposes a version string."""

from __future__ import annotations

import servomexlib


def test_package_imports() -> None:
    """``import servomexlib`` succeeds."""
    assert servomexlib is not None


def test_version_is_nonempty_string() -> None:
    """``servomexlib.__version__`` is a non-empty string."""
    assert isinstance(servomexlib.__version__, str)
    assert servomexlib.__version__
