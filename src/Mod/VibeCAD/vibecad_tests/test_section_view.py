# SPDX-License-Identifier: LGPL-2.1-or-later

"""Pure coverage for the Model ribbon Section View command."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import VibeCADSectionView as section


REPO = Path(__file__).resolve().parents[4]


class _Box:
    def __init__(self, xmin, xmax, ymin, ymax, zmin, zmax, valid=True):
        self.XMin, self.XMax = xmin, xmax
        self.YMin, self.YMax = ymin, ymax
        self.ZMin, self.ZMax = zmin, zmax
        self._valid = valid

    def isValid(self) -> bool:
        return self._valid


class _Object:
    def __init__(self, box=None):
        if box is None:
            self.Shape = SimpleNamespace(BoundBox=None)
        else:
            self.Shape = SimpleNamespace(BoundBox=box)


class _View:
    def __init__(self, clipped=False):
        self.clipped = clipped
        self.calls: list[tuple] = []

    def hasClippingPlane(self) -> bool:
        return self.clipped

    def toggleClippingPlane(self, **kwargs):
        self.calls.append(kwargs)
        toggle = kwargs.get("toggle", -1)
        if toggle == 0:
            self.clipped = False
        elif toggle == 1:
            self.clipped = True
        else:
            self.clipped = not self.clipped


def test_bounds_center_combines_valid_shape_boxes() -> None:
    first = _Object(_Box(0.0, 10.0, 0.0, 4.0, -2.0, 2.0))
    second = _Object(_Box(10.0, 20.0, 4.0, 8.0, 2.0, 6.0))
    invalid = _Object(_Box(0.0, 1.0, 0.0, 1.0, 0.0, 1.0, valid=False))
    empty = _Object()

    assert section.bounds_center((first, second, invalid, empty)) == (
        10.0,
        4.0,
        2.0,
    )


def test_bounds_center_is_none_when_no_renderable_geometry() -> None:
    assert section.bounds_center(()) is None
    assert section.bounds_center((_Object(),)) is None


def test_inactive_without_a_3d_view() -> None:
    assert section.is_section_view_active() is False
    assert section.is_section_view_active(view=SimpleNamespace()) is False


def test_toggle_requires_an_active_3d_view() -> None:
    with pytest.raises(RuntimeError, match="active 3D view"):
        section.toggle_section_view(view=None)


def test_set_section_view_toggles_clip_with_manipulator(monkeypatch) -> None:
    view = _View(clipped=False)
    placement = object()
    monkeypatch.setattr(section, "section_view_placement", lambda document=None: placement)

    assert section.set_section_view(True, view=view) == {"section_view": True}
    assert view.calls == [{"toggle": 1, "noManip": False, "pla": placement}]
    assert section.is_section_view_active(view) is True

    assert section.set_section_view(True, view=view) == {"section_view": True}
    assert view.calls == [{"toggle": 1, "noManip": False, "pla": placement}]

    assert section.toggle_section_view(view=view) == {"section_view": False}
    assert view.calls[-1] == {"toggle": 0}
    assert section.is_section_view_active(view) is False


def test_visible_argument_must_be_a_boolean() -> None:
    with pytest.raises(TypeError, match="boolean"):
        section.set_section_view(1, view=_View())  # type: ignore[arg-type]


def test_ribbon_view_group_includes_section_view_after_grid() -> None:
    ribbon = " ".join(
        (REPO / "src/Gui/VibeCADRibbon.cpp").read_text(encoding="utf-8").split()
    )
    assert (
        '"Std_ViewFitAll", "Std_ViewIsometric", "VibeCAD_ToggleGrid", '
        '"VibeCAD_SectionView"'
    ) in ribbon


def test_native_command_is_registered_next_to_grid() -> None:
    command_view = (REPO / "src/Gui/CommandView.cpp").read_text(encoding="utf-8")
    assert 'Command("VibeCAD_SectionView")' in command_view
    grid = command_view.index("new VibeCADCmdToggleGrid()")
    section_cmd = command_view.index("new VibeCADCmdSectionView()")
    assert grid < section_cmd
