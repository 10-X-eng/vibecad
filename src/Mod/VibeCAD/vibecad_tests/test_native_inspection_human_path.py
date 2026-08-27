# SPDX-License-Identifier: LGPL-2.1-or-later

"""The human Visual Inspection command must use the shared background operation."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_visual_inspection_accepts_through_shared_background_gui() -> None:
    source = (ROOT / "Inspection" / "Gui" / "VisualInspection.cpp").read_text(
        encoding="utf-8"
    )

    accept = source[source.index("void VisualInspection::accept()") :]
    assert 'PyImport_ImportModule("VibeCADInspectionComparisonGui")' in accept
    assert 'callMemberFunction("start_visual_inspection"' in accept
    assert "document->recompute();" not in accept


def test_shared_background_gui_is_packaged() -> None:
    cmake = (ROOT / "VibeCAD" / "CMakeLists.txt").read_text(encoding="utf-8")

    assert "VibeCADInspectionComparisonGui.py" in cmake
