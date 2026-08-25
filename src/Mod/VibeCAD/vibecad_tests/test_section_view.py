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
        self.calls: list[dict] = []

    def hasClippingPlane(self) -> bool:
        return self.clipped

    def getSceneGraph(self):
        return None

    def toggleClippingPlane(self, **kwargs):
        self.calls.append(kwargs)
        toggle = kwargs.get("toggle", -1)
        if toggle == 0:
            self.clipped = False
        elif toggle == 1:
            self.clipped = True
        else:
            self.clipped = not self.clipped


@pytest.fixture(autouse=True)
def _reset_section_settings() -> None:
    section.reset_section_view_settings()
    yield
    section.reset_section_view_settings()


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


def test_principal_planes_match_solidworks_and_fusion() -> None:
    assert section.section_plane_normal("front") == (0.0, 0.0, 1.0)
    assert section.section_plane_normal("top") == (0.0, 1.0, 0.0)
    assert section.section_plane_normal("right") == (1.0, 0.0, 0.0)
    assert section.section_plane_normal("front", flipped=True) == (0.0, 0.0, -1.0)
    assert section.section_plane_normal("top", flipped=True) == (0.0, -1.0, 0.0)
    assert section.section_plane_normal("right", flipped=True) == (-1.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="front, top, or right"):
        section.section_plane_normal("camera")


def test_clip_plane_origin_offsets_along_the_section_normal() -> None:
    center = (10.0, 4.0, 2.0)
    settings = section.SectionViewSettings(plane="front", offset=5.0)
    origin, normal = section.clip_plane_from_settings(settings, center)
    assert normal == (0.0, 0.0, 1.0)
    assert origin == (10.0, 4.0, 7.0)

    flipped = section.SectionViewSettings(plane="right", offset=3.0, flipped=True)
    origin, normal = section.clip_plane_from_settings(flipped, center)
    assert normal == (-1.0, 0.0, 0.0)
    assert origin == (7.0, 4.0, 2.0)


def test_offset_range_follows_the_selected_axis_extent() -> None:
    bounds = section.model_bounds(
        (_Object(_Box(0.0, 20.0, -4.0, 4.0, -10.0, 10.0)),)
    )
    assert bounds is not None
    assert section.section_offset_range(bounds, "front") == (-10.0, 10.0)
    assert section.section_offset_range(bounds, "top") == (-4.0, 4.0)
    assert section.section_offset_range(bounds, "right") == (-10.0, 10.0)


def test_section_plane_corners_are_centered_and_coplanar() -> None:
    origin = (1.0, 2.0, 3.0)
    normal = (0.0, 0.0, 1.0)
    corners = section.section_plane_corners(origin, normal, 4.0, 5.0)
    assert len(corners) == 4
    cx = sum(corner[0] for corner in corners) / 4.0
    cy = sum(corner[1] for corner in corners) / 4.0
    cz = sum(corner[2] for corner in corners) / 4.0
    assert (cx, cy, cz) == pytest.approx(origin)
    for corner in corners:
        assert corner[2] == pytest.approx(3.0)


def test_set_section_view_uses_a_clean_clip_without_a_coin_manipulator(
    monkeypatch,
) -> None:
    view = _View(clipped=False)
    placement = object()
    monkeypatch.setattr(
        section,
        "section_view_placement",
        lambda document=None, settings=None: placement,
    )

    assert section.set_section_view(True, view=view) == {"section_view": True}
    assert view.calls == [{"toggle": 1, "noManip": True, "pla": placement}]
    assert section.is_section_view_active(view) is True

    assert section.set_section_view(True, view=view) == {"section_view": True}
    assert view.calls == [{"toggle": 1, "noManip": True, "pla": placement}]

    assert section.toggle_section_view(view=view, show_ui=False) == {
        "section_view": False
    }
    assert view.calls[-1] == {"toggle": 0}
    assert section.is_section_view_active(view) is False


def test_configure_section_view_reapplies_a_live_cut(monkeypatch) -> None:
    view = _View(clipped=False)
    seen: list[object] = []

    def fake_placement(document=None, settings=None):
        seen.append(settings)
        return object()

    monkeypatch.setattr(section, "section_view_placement", fake_placement)
    section.set_section_view(True, view=view)
    section.configure_section_view(plane="top", offset=8.0, flipped=True, view=view)

    assert view.clipped is True
    assert view.calls[-2] == {"toggle": 0}
    assert view.calls[-1]["toggle"] == 1
    assert view.calls[-1]["noManip"] is True
    assert seen[-1].plane == "top"
    assert seen[-1].offset == 8.0
    assert seen[-1].flipped is True
    assert section.current_section_view_settings().plane == "top"


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
    assert "Front, Top, or Right section plane" in command_view
    assert "draggable section plane" not in command_view


def test_section_view_dialog_matches_solidworks_and_fusion_controls() -> None:
    gui = (
        REPO / "src/Mod/VibeCAD/VibeCADSectionViewGui.py"
    ).read_text(encoding="utf-8")
    helper = (
        REPO / "src/Mod/VibeCAD/VibeCADSectionView.py"
    ).read_text(encoding="utf-8")
    assert "class SectionViewDialog" in gui
    assert 'setObjectName("VibeCADSectionViewDialog")' in gui
    assert "Front (XY)" in gui
    assert "Top (XZ)" in gui
    assert "Right (YZ)" in gui
    assert "Flip" in gui
    assert "Offset" in gui
    assert "noManip=True" in helper or "noManip=True" in gui
    assert "SoClipPlaneManip" not in helper
    assert "show_section_view_dialog" in gui
    assert "close_section_view_dialog" in gui


class _RejectingSbVec3f:
    """Mimic macOS Pivy SWIG rejecting starred-tuple SbVec3f construction."""

    def __init__(self, *args):
        if args:
            raise TypeError(
                "Wrong number or type of arguments for overloaded function "
                "'new_SbVec3f'."
            )
        self.value = (0.0, 0.0, 0.0)

    def setValue(self, x, y, z) -> None:
        self.value = (float(x), float(y), float(z))


def test_section_view_does_not_star_unpack_into_sbvec3f() -> None:
    helper = (REPO / "src/Mod/VibeCAD/VibeCADSectionView.py").read_text(
        encoding="utf-8"
    )
    assert "SbVec3f(*" not in helper
    assert "point.set1Value(index, coin.SbVec3f" not in helper


def test_coin_vec3_uses_empty_constructor_and_setvalue() -> None:
    coin = SimpleNamespace(SbVec3f=_RejectingSbVec3f)
    vec = section._coin_vec3(coin, (10.0, 4.0, -2.5))
    assert vec.value == (10.0, 4.0, -2.5)


def test_install_overlay_writes_corners_as_three_floats() -> None:
    recorded: list[tuple[int, tuple[float, ...]]] = []

    class _Points:
        def set1Value(self, index, *xyz) -> None:
            recorded.append((int(index), tuple(float(v) for v in xyz)))

    class _Node:
        BASE_COLOR = "base"
        LINES = "lines"

        def __init__(self) -> None:
            self.point = _Points()
            self.coordIndex = _Points()
            self.model = None
            self.diffuseColor = SimpleNamespace(setValue=lambda *_a: None)
            self.transparency = SimpleNamespace(setValue=lambda *_a: None)
            self.emissiveColor = SimpleNamespace(setValue=lambda *_a: None)
            self.style = None
            self.lineWidth = None

        def setName(self, _name) -> None:
            return None

        def addChild(self, _child) -> None:
            return None

    class _Scene:
        def insertChild(self, _node, _index) -> None:
            return None

    coin = SimpleNamespace(
        SoSeparator=_Node,
        SoLightModel=_Node,
        SoMaterial=_Node,
        SoCoordinate3=_Node,
        SoIndexedFaceSet=_Node,
        SoDrawStyle=_Node,
        SoIndexedLineSet=_Node,
        SbVec3f=_RejectingSbVec3f,
    )
    corners = section.section_plane_corners((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 10.0, 5.0)
    section._overlay_node = None
    section._install_overlay_node(coin, _Scene(), corners)
    assert [item[0] for item in recorded[:4]] == [0, 1, 2, 3]
    assert all(len(item[1]) == 3 for item in recorded[:4])
