# SPDX-License-Identifier: LGPL-2.1-or-later

"""Repo-relative Aero command icon (no absolute machine paths)."""

from __future__ import annotations

from pathlib import Path

ICON_FILENAME = "vibecad-aero-analyze.svg"


def aero_icon_path() -> str:
    """Return the workbench-relative drone SVG used by Aero commands."""

    return str(Path(__file__).resolve().parent / "icons" / ICON_FILENAME)
