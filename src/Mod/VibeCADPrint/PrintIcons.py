# SPDX-License-Identifier: LGPL-2.1-or-later

"""Repository-relative icons for the 3D Print workbench."""

from __future__ import annotations

from pathlib import Path


_ICONS = {
    "open": "vibecad-print-open.svg",
    "save": "vibecad-print-save.svg",
    "setup": "vibecad-print-setup.svg",
}


def icon_path(name: str) -> str:
    return str(Path(__file__).resolve().parent / "icons" / _ICONS[name])
