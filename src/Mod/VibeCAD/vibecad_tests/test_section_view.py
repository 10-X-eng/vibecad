# SPDX-License-Identifier: LGPL-2.1-or-later

"""Pure coverage for the Model ribbon Section View command."""

from __future__ import annotations

from math import cos, radians, sin
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
    def __init__(self, clipped=False, look=(0.0, 0.0, -1.0)):
        self.clipped = clipped
        self.calls: list[dict] = []
        self._look = look

    def getViewDirection(self):
        return self._look

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
    section._overlay_node = None
    section._cap_node = None
    section._dragger_node = None
    section._dragger_busy = False
    section._triad_parts = None
    section._bounds_view = None
    section._section_bounds = None
    yield
    section.reset_section_view_settings()
    section._overlay_node = None
    section._cap_node = None
    section._dragger_node = None
    section._dragger_busy = False
    section._triad_parts = None
    section._bounds_view = None
    section._section_bounds = None


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


def test_principal_planes_match_vibecad_top_front_right_views() -> None:
    assert section.section_plane_normal("front") == (0.0, 1.0, 0.0)
    assert section.section_plane_normal("top") == (0.0, 0.0, 1.0)
    assert section.section_plane_normal("right") == (1.0, 0.0, 0.0)
    assert section.section_plane_normal("front", flipped=True) == (0.0, -1.0, 0.0)
    assert section.section_plane_normal("top", flipped=True) == (0.0, 0.0, -1.0)
    assert section.section_plane_normal("right", flipped=True) == (-1.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="front, top, or right"):
        section.section_plane_normal("camera")


def test_clip_plane_origin_offsets_along_the_section_normal() -> None:
    center = (10.0, 4.0, 2.0)
    settings = section.SectionViewSettings(plane="top", offset=5.0)
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
    assert section.section_offset_range(bounds, "front") == (-4.0, 4.0)
    assert section.section_offset_range(bounds, "top") == (-10.0, 10.0)
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
        "_placement_from_bounds",
        lambda settings, bounds: placement,
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

    def fake_placement(settings, bounds):
        seen.append(settings)
        return object()

    monkeypatch.setattr(section, "_placement_from_bounds", fake_placement)
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
    assert 'setObjectName("sectionPlaneLabel")' in gui
    assert "sectionOffset" in gui
    assert "Flip" in gui
    assert "planeFront" not in gui
    assert "sectionPitch" not in gui
    assert "Tilt X" not in gui
    assert "VibeCADSectionDragger" in helper
    assert "SoTransformDragger" in helper
    assert "apply_world_rotation" in helper
    assert "noManip=True" in helper or "noManip=True" in gui
    assert "SoClipPlaneManip" not in helper
    assert "show_section_view_dialog" in gui
    assert "close_section_view_dialog" in gui
    assert "RightDockWidgetArea" in gui
    assert "tabifyDockWidget" in gui
    assert "Std_TaskView" in gui
    assert "VibeCADAssistantPanel" in gui
    assert 'setObjectName("VibeCADSectionViewDock")' in gui


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


def test_section_plane_distance_is_the_plane_equation() -> None:
    assert section.section_plane_distance((10.0, 4.0, 7.0), (0.0, 0.0, 1.0)) == 7.0
    assert section.section_plane_distance((8.0, 4.0, 2.0), (1.0, 0.0, 0.0)) == 8.0
    assert section.section_plane_distance((1.0, 2.0, 3.0), (0.0, -1.0, 0.0)) == -2.0


def test_hatch_spacing_scales_with_model_size() -> None:
    small = section.ModelBounds(0.0, 10.0, 0.0, 4.0, 0.0, 2.0)
    large = section.ModelBounds(0.0, 180.0, 0.0, 90.0, 0.0, 40.0)
    assert section.hatch_spacing_for_bounds(None) == pytest.approx(2.5)
    assert section.hatch_spacing_for_bounds(small) >= 0.5
    assert section.hatch_spacing_for_bounds(large) > section.hatch_spacing_for_bounds(small)


def _point_in_rectangle(point, xmin, xmax, ymin, ymax, eps=1e-8) -> bool:
    return xmin - eps <= point[0] <= xmax + eps and ymin - eps <= point[1] <= ymax + eps


def test_hatch_segments_fill_a_rectangle_at_forty_five_degrees() -> None:
    loop = ((0.0, 0.0), (40.0, 0.0), (40.0, 20.0), (0.0, 20.0))
    segments = section.hatch_segments_for_loops((loop,), spacing=5.0, angle_deg=45.0)
    assert len(segments) >= 6
    for start, end in segments:
        assert _point_in_rectangle(start, 0.0, 40.0, 0.0, 20.0)
        assert _point_in_rectangle(end, 0.0, 40.0, 0.0, 20.0)
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = (dx * dx + dy * dy) ** 0.5
        assert length > 0.5
        assert abs(dx / length - dy / length) < 1e-6


def test_hatch_segments_do_not_enter_a_hole() -> None:
    outer = ((0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0))
    hole = ((7.0, 7.0), (13.0, 7.0), (13.0, 13.0), (7.0, 13.0))
    segments = section.hatch_segments_for_loops((outer, hole), spacing=2.0, angle_deg=45.0)
    assert segments
    for start, end in segments:
        mid = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
        assert not _point_in_rectangle(mid, 7.05, 12.95, 7.05, 12.95)


def test_section_caps_are_filled_faces_with_hatch_on_the_cut_plane() -> None:
    origin = (20.0, 10.0, 5.0)
    normal = (0.0, 0.0, 1.0)
    loop = (
        (0.0, 0.0, 5.0),
        (40.0, 0.0, 5.0),
        (40.0, 20.0, 5.0),
        (0.0, 20.0, 5.0),
    )
    caps = section.build_section_cap_geometry((loop,), origin, normal, spacing=5.0)
    assert len(caps.triangles) >= 2
    assert caps.hatch
    assert len(caps.outlines) == 4
    for triangle in caps.triangles:
        assert len(triangle) == 3
        for point in triangle:
            assert point[2] == pytest.approx(5.0 - section.SECTION_CAP_OFFSET)
    for start, end in caps.hatch:
        assert start[2] == pytest.approx(5.0 - section.SECTION_CAP_OFFSET)
        assert end[2] == pytest.approx(5.0 - section.SECTION_CAP_OFFSET)


def test_sectionable_shapes_skip_hidden_and_non_solid_objects() -> None:
    solid = SimpleNamespace(
        Visibility=True,
        ViewObject=SimpleNamespace(Visibility=True),
        Shape=SimpleNamespace(isNull=lambda: False, Solids=(object(),)),
    )
    hidden = SimpleNamespace(
        Visibility=False,
        ViewObject=SimpleNamespace(Visibility=True),
        Shape=SimpleNamespace(isNull=lambda: False, Solids=(object(),)),
    )
    shell = SimpleNamespace(
        Visibility=True,
        ViewObject=SimpleNamespace(Visibility=True),
        Shape=SimpleNamespace(isNull=lambda: False, Solids=()),
    )
    parent = SimpleNamespace(
        Visibility=True,
        ViewObject=SimpleNamespace(Visibility=True),
        Shape=SimpleNamespace(isNull=lambda: False, Solids=(object(),)),
    )
    child = SimpleNamespace(
        Visibility=True,
        ViewObject=SimpleNamespace(Visibility=True),
        Shape=SimpleNamespace(isNull=lambda: False, Solids=(object(),)),
        getParentGeoFeatureGroup=lambda: parent,
    )
    hidden_parent = SimpleNamespace(
        Visibility=False,
        ViewObject=SimpleNamespace(Visibility=False),
        Shape=SimpleNamespace(isNull=lambda: False, Solids=(object(),)),
    )
    visible_child = SimpleNamespace(
        Visibility=True,
        ViewObject=SimpleNamespace(Visibility=True),
        Shape=SimpleNamespace(isNull=lambda: False, Solids=(object(),)),
        getParentGeoFeatureGroup=lambda: hidden_parent,
    )
    assert section.iter_sectionable_shapes((solid, hidden, shell, child, visible_child)) == (
        solid.Shape,
        visible_child.Shape,
    )


def test_sync_overlay_installs_hatched_caps_not_just_the_section_plane(monkeypatch) -> None:
    recorded_names: list[str] = []
    inserted: list[object] = []
    recorded: list[tuple[int, tuple[float, ...]]] = []

    class _Points:
        def set1Value(self, index, *xyz) -> None:
            recorded.append((int(index), tuple(float(v) for v in xyz)))

    class _Node:
        BASE_COLOR = "base"
        LINES = "lines"
        COUNTERCLOCKWISE = "ccw"
        UNKNOWN_ORDERING = "unknown_order"
        UNKNOWN_SHAPE_TYPE = "unknown"
        UNKNOWN_FACE_TYPE = "unknown_face"

        def __init__(self) -> None:
            self.point = _Points()
            self.coordIndex = _Points()
            self.model = None
            self.diffuseColor = SimpleNamespace(setValue=lambda *_a: None)
            self.transparency = SimpleNamespace(setValue=lambda *_a: None)
            self.emissiveColor = SimpleNamespace(setValue=lambda *_a: None)
            self.style = None
            self.lineWidth = None
            self.vertexOrdering = None
            self.shapeType = None
            self.faceType = None
            self._name = ""
            self.children: list[object] = []

        def setName(self, name) -> None:
            self._name = str(name)
            recorded_names.append(self._name)

        def addChild(self, child) -> None:
            self.children.append(child)

    class _Scene:
        def __init__(self) -> None:
            self.children: list[object] = []

        def insertChild(self, node, index) -> None:
            inserted.append(node)
            self.children.insert(int(index), node)

        def findChild(self, node) -> int:
            try:
                return self.children.index(node)
            except ValueError:
                return -1

        def removeChild(self, node) -> None:
            if node in self.children:
                self.children.remove(node)

        def getChildren(self):
            return tuple(self.children)

    coin = SimpleNamespace(
        SoSeparator=_Node,
        SoLightModel=_Node,
        SoMaterial=_Node,
        SoCoordinate3=_Node,
        SoIndexedFaceSet=_Node,
        SoIndexedLineSet=_Node,
        SoDrawStyle=_Node,
        SoShapeHints=_Node,
        SbVec3f=_RejectingSbVec3f,
    )
    scene = _Scene()
    view = SimpleNamespace(getSceneGraph=lambda: scene)
    caps = section.build_section_cap_geometry(
        (
            (
                (0.0, 0.0, 5.0),
                (40.0, 0.0, 5.0),
                (40.0, 20.0, 5.0),
                (0.0, 20.0, 5.0),
            ),
        ),
        (20.0, 10.0, 5.0),
        (0.0, 0.0, 1.0),
        spacing=5.0,
    )
    monkeypatch.setattr(section, "model_bounds", lambda _objects: section.ModelBounds(0, 40, 0, 20, 0, 10))
    monkeypatch.setattr(
        section, "_schedule_cap_build",
        lambda coin, view, *_args: section._install_cap_node(coin, view.getSceneGraph(), caps),
    )
    monkeypatch.setattr(
        section,
        "_document_objects",
        lambda _document: (_Object(_Box(0, 40, 0, 20, 0, 10)),),
    )

    import sys
    import types

    pivy = types.ModuleType("pivy")
    pivy.coin = coin
    monkeypatch.setitem(sys.modules, "pivy", pivy)
    monkeypatch.setitem(sys.modules, "pivy.coin", coin)

    section._overlay_node = None
    section._cap_node = None
    section._sync_overlay(view, None, section.SectionViewSettings(show_plane=True))

    assert "VibeCADSectionPlaneOverlay" in recorded_names
    assert "VibeCADSectionCapOverlay" in recorded_names
    assert any(len(item[1]) == 3 for item in recorded[4:])
    assert len(inserted) == 2

    recorded_names.clear()
    inserted.clear()
    section._sync_overlay(view, None, section.SectionViewSettings(show_plane=False))
    assert "VibeCADSectionPlaneOverlay" not in recorded_names
    assert "VibeCADSectionCapOverlay" in recorded_names


def test_clip_plane_alone_cannot_represent_a_solid_cut() -> None:
    helper = (REPO / "src/Mod/VibeCAD/VibeCADSectionView.py").read_text(
        encoding="utf-8"
    )
    assert "section_cap_geometry_from_objects" in helper
    assert "hatch_segments_for_loops" in helper
    assert "VibeCADSectionCapOverlay" in helper


def test_principal_plane_for_axis_picks_the_dominant_world_axis() -> None:
    assert section.principal_plane_for_axis((1.0, 0.0, 0.0)) == "right"
    assert section.principal_plane_for_axis((0.0, 1.0, 0.0)) == "front"
    assert section.principal_plane_for_axis((0.0, 0.0, 1.0)) == "top"
    assert section.principal_plane_for_axis((-0.2, 0.1, 0.97)) == "top"


def test_untilted_top_cs_matches_world_xyz() -> None:
    u_axis, v_axis, normal = section.section_cs_axes("top")
    assert u_axis == pytest.approx((1.0, 0.0, 0.0))
    assert v_axis == pytest.approx((0.0, 1.0, 0.0))
    assert normal == pytest.approx((0.0, 0.0, 1.0))


def test_top_pitch_tilts_the_section_normal() -> None:
    normal = section.section_plane_normal("top", pitch=30.0)
    assert normal[0] == pytest.approx(0.0, abs=1e-9)
    assert normal[2] == pytest.approx(cos(radians(30.0)))
    assert abs(normal[1]) == pytest.approx(sin(radians(30.0)))
    assert normal != (0.0, 0.0, 1.0)


def test_dragger_z_arrow_slides_offset_on_the_current_plane() -> None:
    settings = section.SectionViewSettings(plane="top", offset=0.0)
    updated = section.apply_dragger_translation(
        settings,
        origin=(10.0, 4.0, 7.0),
        center=(10.0, 4.0, 2.0),
        translation_counts=(0, 0, 4),
    )
    assert updated.plane == "top"
    assert updated.offset == pytest.approx(5.0)
    assert updated.yaw == 0.0
    assert updated.pitch == 0.0


def test_dragger_x_arrow_switches_to_the_right_plane() -> None:
    settings = section.SectionViewSettings(plane="top", offset=8.0, yaw=12.0, pitch=-5.0)
    updated = section.apply_dragger_translation(
        settings,
        origin=(13.0, 4.0, 2.0),
        center=(10.0, 4.0, 2.0),
        translation_counts=(30, 0, 0),
    )
    assert updated.plane == "right"
    assert updated.offset == pytest.approx(3.0)
    assert updated.yaw == 0.0
    assert updated.pitch == 0.0


def test_dragger_y_arrow_switches_to_the_front_plane() -> None:
    settings = section.SectionViewSettings(plane="top")
    updated = section.apply_dragger_translation(
        settings,
        origin=(10.0, 9.0, 2.0),
        center=(10.0, 4.0, 2.0),
        translation_counts=(0, 30, 0),
    )
    assert updated.plane == "front"
    assert updated.offset == pytest.approx(5.0)


def test_dragger_rotation_rings_change_plane_angle() -> None:
    settings = section.SectionViewSettings(plane="top", offset=0.0)
    updated = section.apply_dragger_rotation(
        settings,
        origin=(10.0, 4.0, 2.0),
        center=(10.0, 4.0, 2.0),
        rotation_counts=(15, -10, 0),
    )
    assert updated.pitch == pytest.approx(15.0)
    assert updated.yaw == pytest.approx(-10.0)
    assert updated.plane == "top"
    assert section.section_plane_normal(
        updated.plane, yaw=updated.yaw, pitch=updated.pitch
    ) != (0.0, 0.0, 1.0)


def test_remove_overlay_keeps_the_datum_dragger() -> None:
    class _Scene:
        def __init__(self) -> None:
            self.children = ["overlay", "dragger"]

        def findChild(self, node) -> int:
            try:
                return self.children.index(node)
            except ValueError:
                return -1

        def removeChild(self, node) -> None:
            self.children.remove(node)

    scene = _Scene()
    view = SimpleNamespace(getSceneGraph=lambda: scene)
    section._overlay_node = "overlay"
    section._cap_node = None
    section._dragger_node = "dragger"
    section._remove_overlay(view)
    assert scene.children == ["dragger"]
    assert section._dragger_node == "dragger"


def test_section_gizmo_does_not_register_pivy_dragger_callbacks() -> None:
    helper = (
        REPO / "src/Mod/VibeCAD/VibeCADSectionView.py"
    ).read_text(encoding="utf-8")
    assert "addStartCallback" not in helper
    assert "addValueChangedCallback" not in helper
    assert "addFinishCallback" not in helper
    assert "_start_dragger_poll" in helper
    assert "_DRAGGER_NDC_SIZE = 0.03" in helper


def test_preview_clip_does_not_rebuild_scene_overlay(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(section, "_update_clip_plane", lambda *_a, **_k: True)
    monkeypatch.setattr(section, "is_section_view_active", lambda view=None: True)
    monkeypatch.setattr(section, "_placement_from_bounds", lambda *_a, **_k: object())
    monkeypatch.setattr(
        section, "_sync_overlay", lambda *_a, **_k: calls.append("overlay")
    )
    monkeypatch.setattr(
        section, "_sync_dragger", lambda *_a, **_k: calls.append("dragger")
    )
    view = SimpleNamespace()
    section._apply_clip(view, None, section.SectionViewSettings(), preview=True)
    assert calls == []
    section._apply_clip(view, None, section.SectionViewSettings(), preview=False)
    assert calls == ["overlay", "dragger"]


def test_identity_quaternion_keeps_the_section_normal() -> None:
    assert section._quat_rotate((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0)) == pytest.approx(
        (0.0, 0.0, 1.0)
    )


def test_section_offset_label_names_the_active_axis() -> None:
    assert "Y" in section.section_offset_label(section.SectionViewSettings(plane="front"))
    assert "Z" in section.section_offset_label(section.SectionViewSettings(plane="top"))
    assert "X" in section.section_offset_label(section.SectionViewSettings(plane="right"))
    tilted = section.SectionViewSettings(plane="top", pitch=25.0)
    assert "cut" in section.section_offset_label(tilted)


def test_orientation_roundtrip_preserves_the_cut_normal() -> None:
    for plane in ("front", "top", "right"):
        for yaw, pitch, roll in (
            (0.0, 0.0, 0.0),
            (20.0, 0.0, 0.0),
            (0.0, -35.0, 0.0),
            (0.0, 0.0, 40.0),
            (15.0, -20.0, 30.0),
        ):
            u_axis, _v_axis, normal = section.section_cs_axes(
                plane, False, yaw, pitch, roll
            )
            yaw2, pitch2, roll2 = section.orientation_from_axes(
                plane, False, u_axis, normal
            )
            _u2, _v2, normal2 = section.section_cs_axes(
                plane, False, yaw2, pitch2, roll2
            )
            assert normal2 == pytest.approx(normal, abs=1.0e-6)
            u2, _v2, _n2 = section.section_cs_axes(plane, False, yaw2, pitch2, roll2)
            assert u2 == pytest.approx(u_axis, abs=1.0e-5)


def test_world_rotation_tilts_instead_of_snapping_to_a_principal_plane() -> None:
    settings = section.SectionViewSettings(plane="top", offset=0.0)
    u_axis, _v_axis, normal = section.section_cs_axes("top", False, 0.0, 25.0, 0.0)
    # Quaternion mapping +Z to the tilted normal and +X to u.
    quat = _axes_to_quat(u_axis, _cross(normal, u_axis), normal)
    updated = section.apply_world_rotation(
        settings,
        origin=(10.0, 4.0, 2.0),
        center=(10.0, 4.0, 2.0),
        quat=quat,
    )
    assert updated.plane == "top"
    assert abs(updated.pitch) > 10.0
    tilted = section.section_plane_normal(
        updated.plane, yaw=updated.yaw, pitch=updated.pitch, roll=updated.roll
    )
    assert tilted == pytest.approx(normal, abs=1.0e-5)


def _cross(left, right):
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _axes_to_quat(u_axis, v_axis, normal):
    m00, m01, m02 = u_axis[0], v_axis[0], normal[0]
    m10, m11, m12 = u_axis[1], v_axis[1], normal[1]
    m20, m21, m22 = u_axis[2], v_axis[2], normal[2]
    trace = m00 + m11 + m22
    if trace > 0.0:
        scale = (trace + 1.0) ** 0.5 * 2.0
        w = 0.25 * scale
        x = (m21 - m12) / scale
        y = (m02 - m20) / scale
        z = (m10 - m01) / scale
    elif m00 > m11 and m00 > m22:
        scale = (1.0 + m00 - m11 - m22) ** 0.5 * 2.0
        w = (m21 - m12) / scale
        x = 0.25 * scale
        y = (m01 + m10) / scale
        z = (m02 + m20) / scale
    elif m11 > m22:
        scale = (1.0 + m11 - m00 - m22) ** 0.5 * 2.0
        w = (m02 - m20) / scale
        x = (m01 + m10) / scale
        y = 0.25 * scale
        z = (m12 + m21) / scale
    else:
        scale = (1.0 + m22 - m00 - m11) ** 0.5 * 2.0
        w = (m10 - m01) / scale
        x = (m02 + m20) / scale
        y = (m12 + m21) / scale
        z = 0.25 * scale
    return (x, y, z, w)


def test_tiny_axis_jitter_does_not_switch_planes() -> None:
    settings = section.SectionViewSettings(plane="top", offset=5.0)
    updated = section.apply_dragger_translation(
        settings,
        origin=(10.1, 4.0, 7.0),
        center=(10.0, 4.0, 2.0),
        translation_counts=(1, 0, 0),
    )
    assert updated.plane == "top"


@pytest.mark.parametrize("look", [(0, 0, -1), (0, 0, 1), (0, -1, 0), (1, 0, 0), (0.2, 0.9, 0.1)])
def test_section_plane_from_view_keeps_half_away_from_camera(look) -> None:
    settings = section.initial_section_settings(look)
    origin, normal = section.clip_plane_from_settings(settings, (0, 0, 0))
    # SoClipPlane keeps its positive half-space. The camera must be in
    # the removed half, so the cut face opens toward the viewer.
    assert section._dot(normal, look) > 0


def test_initial_settings_follow_the_camera_look_direction() -> None:
    settings = section.initial_section_settings((0.2, 0.9, 0.1))
    assert settings.plane == "front"
    assert settings.offset == 0.0


def test_world_rotation_does_not_reverse_the_kept_half() -> None:
    settings = section.SectionViewSettings(plane="top", flipped=False)
    opposite = section._quat_rotate((1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    assert opposite[2] == pytest.approx(-1.0)
    updated = section.apply_world_rotation(
        settings,
        origin=(10.0, 4.0, 2.0),
        center=(10.0, 4.0, 2.0),
        quat=(1.0, 0.0, 0.0, 0.0),
    )
    normal = section.section_plane_normal(
        updated.plane,
        updated.flipped,
        updated.yaw,
        updated.pitch,
        updated.roll,
    )
    assert normal[2] == pytest.approx(1.0, abs=1.0e-5)
    assert updated.flipped is False


def test_offset_through_point_keeps_the_current_plane() -> None:
    settings = section.SectionViewSettings(plane="top", offset=0.0)
    offset = section.offset_through_point(settings, (10.0, 4.0, 2.0), (12.0, 8.0, 9.0))
    assert offset == pytest.approx(7.0)
    front = section.SectionViewSettings(plane="front")
    assert section.offset_through_point(front, (10.0, 4.0, 2.0), (12.0, 8.0, 9.0)) == pytest.approx(4.0)


def test_snap_point_from_a_hole_uses_the_circle_or_cylinder_center() -> None:
    edge = SimpleNamespace(
        ShapeType="Edge",
        Curve=SimpleNamespace(Center=SimpleNamespace(x=5.0, y=6.0, z=7.0)),
        CenterOfMass=SimpleNamespace(x=0.0, y=0.0, z=0.0),
    )
    face = SimpleNamespace(
        ShapeType="Face",
        Surface=SimpleNamespace(
            Center=SimpleNamespace(x=1.0, y=2.0, z=3.0),
            Axis=SimpleNamespace(x=0.0, y=0.0, z=1.0),
        ),
        CenterOfMass=SimpleNamespace(x=9.0, y=9.0, z=4.0),
    )
    vertex = SimpleNamespace(
        ShapeType="Vertex",
        Point=SimpleNamespace(x=4.0, y=5.0, z=6.0),
    )
    assert section.snap_point_from_shape(edge) == (5.0, 6.0, 7.0)
    assert section.snap_point_from_shape(face) == (9.0, 9.0, 4.0)
    assert section.snap_point_from_shape(vertex) == (4.0, 5.0, 6.0)


def test_hole_snap_cuts_along_the_hole_depth() -> None:
    settings = section.SectionViewSettings(plane="top", offset=0.0)
    # Vertical hole: Top plane would cut a circle; switch to a plane that
    # contains the hole axis so the cut follows the hole depth.
    updated = section.apply_feature_snap(
        settings,
        model_center=(10.0, 10.0, 5.0),
        point=(10.0, 10.0, 5.0),
        axis=(0.0, 0.0, 1.0),
        view_direction=(0.0, 0.0, -1.0),
    )
    assert updated.plane in {"front", "right"}
    assert updated.plane != "top"
    assert updated.offset == pytest.approx(0.0)
    # Already on a plane that contains the axis: keep it and only move offset.
    along = section.SectionViewSettings(plane="front", offset=0.0)
    moved = section.apply_feature_snap(
        along,
        model_center=(0.0, 0.0, 0.0),
        point=(4.0, 8.0, 2.0),
        axis=(0.0, 0.0, 1.0),
        view_direction=(0.0, -1.0, 0.0),
    )
    assert moved.plane == "front"
    assert moved.offset == pytest.approx(8.0)


def test_look_direction_is_read_from_getViewDirection() -> None:
    view = SimpleNamespace(getViewDirection=lambda: SimpleNamespace(x=0.0, y=-1.0, z=0.0))
    assert section._view_look_direction(view) == pytest.approx((0.0, -1.0, 0.0))
    settings = section.initial_section_settings(section._view_look_direction(view))
    assert settings.plane == "front"


def test_toggle_starts_on_the_active_view_plane(monkeypatch) -> None:
    front_view = _View(look=(0.0, -1.0, 0.0))
    monkeypatch.setattr(
        section, "_placement_from_bounds", lambda settings, bounds: object()
    )
    section.toggle_section_view(view=front_view, show_ui=False)
    assert section.current_section_view_settings().plane == "front"
    section.set_section_view(False, view=front_view)

    top_view = _View(look=(0.0, 0.0, -1.0))
    section.toggle_section_view(view=top_view, show_ui=False)
    assert section.current_section_view_settings().plane == "top"


def test_look_direction_reads_coin_vectors_with_getvalue() -> None:
    class _CoinVec:
        def getValue(self):
            return (0.0, -1.0, 0.0)

    view = SimpleNamespace(getViewDirection=lambda: _CoinVec())
    assert section._view_look_direction(view) == pytest.approx((0.0, -1.0, 0.0))


def test_look_direction_reads_indexable_vectors() -> None:
    class _IndexVec:
        def __getitem__(self, index):
            return (1.0, 0.0, 0.0)[index]

    view = SimpleNamespace(getViewDirection=lambda: _IndexVec())
    assert section._view_look_direction(view) == pytest.approx((1.0, 0.0, 0.0))
    assert section.initial_section_settings(
        section._view_look_direction(view)
    ).plane == "right"


def test_closing_section_document_releases_scene_before_view_destruction(monkeypatch):
    owner = object()
    other = object()
    view = _View(clipped=True)
    calls = []
    monkeypatch.setattr(section, "_dragger_view", view, raising=False)
    monkeypatch.setattr(section, "_dragger_document", owner, raising=False)
    monkeypatch.setattr(section, "_stop_selection_snap", lambda: calls.append("selection"))
    monkeypatch.setattr(section, "_close_ui", lambda: calls.append("dialog"))
    monkeypatch.setattr(section, "_remove_overlay", lambda v: calls.append(("overlay", v)))
    monkeypatch.setattr(section, "_remove_dragger", lambda v: calls.append(("dragger", v)))
    observer = section._SectionViewDocumentObserver()
    observer.slotDeletedDocument(SimpleNamespace(Document=other))
    assert calls == []
    observer.slotDeletedDocument(SimpleNamespace(Document=owner))
    assert calls == ["selection", ("dragger", view), ("overlay", view), "dialog"]
    assert section._dragger_view is None
    assert section._dragger_document is None


def test_section_poll_pauses_when_another_document_is_active(monkeypatch):
    owner = object()
    calls = []
    monkeypatch.setattr(section, "_dragger_document", owner)
    monkeypatch.setattr(section, "_dragger_node", object())
    monkeypatch.setattr(section, "_stop_dragger_poll", lambda: calls.append("stop"))
    monkeypatch.setattr(section, "_start_dragger_poll", lambda: calls.append("start"))
    observer = section._SectionViewDocumentObserver()
    observer.slotActivateDocument(SimpleNamespace(Document=object()))
    observer.slotActivateDocument(SimpleNamespace(Document=owner))
    assert calls == ["stop", "start"]


def test_new_section_view_releases_previous_scene_first(monkeypatch):
    previous = _View(clipped=True)
    current = _View()
    calls = []
    monkeypatch.setattr(section, "_dragger_view", previous)
    monkeypatch.setattr(section, "set_section_view", lambda value, **kw: calls.append((value, kw["view"])))
    monkeypatch.setattr(section, "_placement_from_bounds", lambda *_: object())
    monkeypatch.setattr(section, "_sync_overlay", lambda *_a, **_kw: calls.append("overlay"))
    monkeypatch.setattr(section, "_sync_dragger", lambda *_a, **_kw: calls.append("dragger"))
    section._apply_clip(current, None, section.SectionViewSettings())
    assert calls == [(False, previous), "overlay", "dragger"]


def test_drag_preview_reuses_bounds_instead_of_rescanning_document(monkeypatch):
    view = _View(clipped=True)
    bounds = section.ModelBounds(0, 10, 0, 20, 0, 30)
    monkeypatch.setattr(section, "_bounds_view", view, raising=False)
    monkeypatch.setattr(section, "_section_bounds", bounds, raising=False)
    monkeypatch.setattr(section, "_placement_from_bounds", lambda settings, cached: cached, raising=False)
    monkeypatch.setattr(section, "model_bounds", lambda *_: pytest.fail("Preview rescanned document shapes"))
    monkeypatch.setattr(section, "_update_clip_plane", lambda v, placement: placement is bounds)
    section._apply_clip(view, None, section.SectionViewSettings(), preview=True)


def test_full_section_update_refreshes_cached_bounds_once(monkeypatch):
    view = _View(clipped=True)
    bounds = section.ModelBounds(0, 10, 0, 20, 0, 30)
    calls = []
    monkeypatch.setattr(section, "model_bounds", lambda *_: calls.append("bounds") or bounds)
    monkeypatch.setattr(section, "_placement_from_bounds", lambda *_: object(), raising=False)
    monkeypatch.setattr(section, "_update_clip_plane", lambda *_: True)
    monkeypatch.setattr(section, "_sync_overlay", lambda *_a, **_k: calls.append(section._bounds_for_view(view, None)))
    monkeypatch.setattr(section, "_sync_dragger", lambda *_a, **_k: calls.append(section._bounds_for_view(view, None)))
    section._apply_clip(view, None, section.SectionViewSettings())
    assert calls == ["bounds", bounds, bounds]


def test_cap_schedule_uses_native_display_without_document_shape_copy(monkeypatch):
    import sys
    backend = _NativeSectionBackend()
    backend.sectionDisplaySizes = lambda handle: (0, 0, 0)
    backend.readSectionDisplay = lambda *args: ()
    monkeypatch.setitem(sys.modules, "PartGui", backend)
    monkeypatch.setattr(section, "App", SimpleNamespace(Vector=lambda *xyz: xyz))
    monkeypatch.setattr(section, "_cap_worker", None)
    snapshots, published = [], []

    def capture(view, *, mesh=False):
        assert mesh
        snapshots.append(view)
        yield ("cached-shape", "display-matrix")

    monkeypatch.setattr(section, "_rendered_section_snapshot_steps", capture)
    monkeypatch.setattr(section, "iter_sectionable_shapes", lambda *args: pytest.fail("live shape access"))
    monkeypatch.setattr(section, "_scene_from_view", lambda view: "scene")
    monkeypatch.setattr(section, "_install_cap_node_steps", lambda *args: published.append(args[-1]))
    view = object()
    section._schedule_cap_build(None, view, object(), (0, 0, 0), (0, 0, 1), 1.0)
    assert not snapshots
    assert not backend.requests
    backend.queued.pop(0)()
    assert snapshots == [view]
    assert backend.requests[-1][1] == [("cached-shape", "display-matrix")]
    backend.requests[-1][-1](object(), "")
    assert len(published) == 1
    assert not published[0].triangles


def test_removing_overlay_cancels_pending_caps(monkeypatch):
    cancelled = []
    monkeypatch.setattr(section, "_cap_worker", SimpleNamespace(cancel=lambda: cancelled.append(True)), raising=False)
    section._remove_overlay(_View())
    assert cancelled == [True]


@pytest.mark.parametrize("kind", ["points", "indexes"])
def test_cap_scene_writes_yield_before_large_buffers_finish(kind):
    writes = []
    field = SimpleNamespace(set1Value=lambda *args: writes.append(args))
    if kind == "points":
        steps = section._write_points_steps(SimpleNamespace(point=field), [(1, 2, 3)] * 700)
    else:
        steps = section._write_indexes_steps(SimpleNamespace(coordIndex=field), list(range(700)))
    next(steps)
    assert 0 < len(writes) <= 256
    list(steps)
    assert len(writes) == 700


def test_model_change_invalidates_inflight_cap_snapshot(monkeypatch):
    owner = object()
    cancelled = []
    monkeypatch.setattr(section, "_dragger_document", owner)
    monkeypatch.setattr(section, "_cap_dirty", False, raising=False)
    monkeypatch.setattr(section, "_cap_worker", SimpleNamespace(cancel=lambda: cancelled.append(True)))
    observer = section._SectionCapDocumentObserver()
    observer.slotChangedObject(SimpleNamespace(Document=object()), "Shape")
    observer.slotChangedObject(SimpleNamespace(Document=owner), "Label")
    assert cancelled == []
    observer.slotChangedObject(SimpleNamespace(Document=owner), "Shape")
    assert cancelled == [True]
    assert section._cap_dirty


def test_section_enable_uses_render_bounds_without_shape_scan(monkeypatch):
    view = _View(clipped=True)
    bounds = section.ModelBounds(0, 10, 0, 20, 0, 30)
    monkeypatch.setattr(section, "_render_bounds", lambda v: bounds, raising=False)
    monkeypatch.setattr(section, "model_bounds", lambda *_: pytest.fail("Render section traversed document shapes"))
    monkeypatch.setattr(section, "_placement_from_bounds", lambda settings, cached: cached)
    monkeypatch.setattr(section, "_update_clip_plane", lambda v, placement: placement is bounds)
    monkeypatch.setattr(section, "_sync_overlay", lambda *_a, **_kw: None)
    monkeypatch.setattr(section, "_sync_dragger", lambda *_a, **_kw: None)
    section._apply_clip(view, None, section.SectionViewSettings())
    assert section._section_bounds == bounds


def test_idle_dragger_does_not_request_repeated_scene_redraws(monkeypatch):
    writes = []
    sizes = iter((3.0, 3.0, 3.00000001, 4.0))
    monkeypatch.setattr(section, "_dragger_node", object())
    monkeypatch.setattr(section, "_last_dragger_scale", None, raising=False)
    monkeypatch.setattr(section, "world_scale_for_ndc", lambda *_: next(sizes))
    monkeypatch.setattr(section, "_set_scale_factor", lambda node, scale: writes.append(scale) or True)
    for _ in range(4):
        section._autoscale_dragger(object(), (0, 0, 0))
    assert writes == [3.0, 4.0]


def test_solid_check_does_not_materialize_every_solid_wrapper():
    class Shape:
        def isNull(self):
            return False

        def countElement(self, kind):
            assert kind == "Solid"
            return 4000

        @property
        def Solids(self):
            pytest.fail("A visibility check materialized thousands of solids")

    assert section._shape_has_solids(Shape())


def test_native_display_sequences_fetch_bounded_chunks_lazily():
    handle = object()
    calls = []
    values = tuple(((float(i), 0., 0.), (float(i), 1., 0.)) for i in range(600))

    def read(actual_handle, kind, first, count):
        assert actual_handle is handle
        assert kind == "hatch"
        assert count <= 256
        calls.append((first, count))
        return values[first:first + count]

    primitives = section._SectionDisplaySequence(handle, "hatch", 600, read)
    assert len(primitives) == 600
    assert bool(primitives)
    assert calls == []
    iterator = iter(primitives)
    assert next(iterator) == values[0]
    assert calls == [(0, 256)]
    assert tuple(iterator) == values[1:]
    assert calls == [(0, 256), (256, 256), (512, 88)]
    assert tuple(primitives) == values


def test_empty_native_display_sequence_does_not_read():
    def read(*args):
        pytest.fail("empty geometry must not fetch a chunk")

    primitives = section._SectionDisplaySequence(object(), "triangles", 0, read)
    assert not primitives
    assert tuple(primitives) == ()


class _NativeSectionBackend:
    def __init__(self):
        self.queued = []
        self.requests = []
        self.cancelled = 0

    def createSectionFaceController(self):
        return object()

    def cancelSectionFaces(self, handle):
        self.cancelled += 1

    def _deferSectionDisplay(self, callback):
        self.queued.append(callback)
        return True

    def requestSectionDisplay(self, *args):
        self.requests.append(args)

    def requestSectionMeshDisplay(self, *args):
        self.requests.append(args)


def test_mesh_cap_worker_uses_mesh_request_without_exact_geometry():
    backend = _NativeSectionBackend()
    received = []
    backend.requestSectionMeshDisplay = lambda *args: received.append(args)
    worker = section._NativeSectionCapWorker(backend)
    worker.request(iter([("mesh", "matrix")]), (0, 0, 0), (0, 0, 1), 1,
                   lambda *_: None, mesh=True)
    backend.queued.pop(0)()
    assert received[0][1] == [("mesh", "matrix")]
    assert not backend.requests


def test_native_cap_worker_cancels_capture_and_rejects_stale_delivery():
    backend = _NativeSectionBackend()
    worker = section._NativeSectionCapWorker(backend)
    published = []
    worker.request(iter([("old", "matrix")]), (0, 0, 0), (0, 0, 1), 1, published.append)
    assert not backend.requests
    worker.cancel()
    backend.queued.pop(0)()
    assert not backend.requests
    worker.request(iter([("new", "matrix")]), (0, 0, 0), (0, 0, 1), 1, published.append)
    backend.queued.pop(0)()
    assert backend.requests[-1][1] == [("new", "matrix")]
    callback = backend.requests[-1][-1]
    worker.cancel()
    callback(object(), "")
    assert published == []
    assert not worker._running


def test_native_cap_worker_adoption_yields_and_cancels():
    backend = _NativeSectionBackend()
    worker = section._NativeSectionCapWorker(backend)
    adopted = []

    def publish(handle):
        adopted.append("started")
        yield
        adopted.append("finished")

    worker.request(iter([]), (0, 0, 0), (0, 0, 1), 1, publish)
    backend.queued.pop(0)()
    backend.requests[-1][-1](object(), "")
    assert adopted == []
    backend.queued.pop(0)()
    assert adopted == ["started"]
    worker.cancel()
    backend.queued.pop(0)()
    assert adopted == ["started"]
    assert not worker._running


def test_native_cap_worker_keeps_valid_caps_when_another_solid_fails(monkeypatch):
    backend = _NativeSectionBackend()
    worker = section._NativeSectionCapWorker(backend)
    warnings, published = [], []
    monkeypatch.setattr(section, "App", SimpleNamespace(
        Console=SimpleNamespace(PrintWarning=warnings.append),
    ))
    worker.request(iter([]), (0, 0, 0), (0, 0, 1), 1, published.append)
    backend.queued.pop(0)()
    geometry = object()
    backend.requests[-1][-1](geometry, "invalid solid")
    assert published == [geometry]
    assert len(warnings) == 1
    assert "invalid solid" in warnings[0]
    assert not worker._running


class _DeletedSectionView:
    def __getattribute__(self, name):
        raise RuntimeError(f"Cannot access attribute '{name}' of deleted object")


def test_scene_lookup_handles_a_deleted_view_wrapper():
    assert section._scene_from_view(_DeletedSectionView()) is None


def test_document_close_cleans_section_state_after_view_was_deleted(monkeypatch):
    document = object()
    events = []
    monkeypatch.setattr(section, "_dragger_document", document)
    monkeypatch.setattr(section, "_dragger_view", _DeletedSectionView())
    for name in ("_dragger_node", "_overlay_node", "_cap_node"):
        monkeypatch.setattr(section, name, object())
    monkeypatch.setattr(section, "_cap_worker", SimpleNamespace(cancel=lambda: events.append("cancel")))
    monkeypatch.setattr(section, "_stop_dragger_poll", lambda: events.append("stop"))
    monkeypatch.setattr(section, "_stop_selection_snap", lambda: None)
    monkeypatch.setattr(section, "_close_ui", lambda: events.append("close"))
    section._SectionViewDocumentObserver().slotDeletedDocument(SimpleNamespace(Document=document))
    assert events == ["stop", "cancel", "close"]
    assert section._dragger_view is None
    assert section._dragger_document is None
    assert section._dragger_node is None
    assert section._overlay_node is None
    assert section._cap_node is None


def _section_dialog_without_gui(monkeypatch):
    import importlib.util
    import sys
    monkeypatch.setitem(sys.modules, "FreeCADGui", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "PySide", SimpleNamespace(
        QtCore=SimpleNamespace(), QtWidgets=SimpleNamespace(QWidget=object),
    ))
    path = REPO / "src/Mod/VibeCAD/VibeCADSectionViewGui.py"
    spec = importlib.util.spec_from_file_location("section_dialog_unit_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return object.__new__(module.SectionViewDialog)


def test_section_dialog_range_uses_the_displayed_section_bounds(monkeypatch):
    import sys
    dialog = _section_dialog_without_gui(monkeypatch)
    class Document:
        @property
        def Objects(self):
            pytest.fail("Slider refresh must not read document shapes")
    monkeypatch.setitem(sys.modules, "FreeCAD", SimpleNamespace(ActiveDocument=Document()))
    view = object()
    monkeypatch.setattr(section, "_active_3d_view", lambda: view)
    monkeypatch.setattr(section, "_bounds_view", view)
    monkeypatch.setattr(section, "_section_bounds", section.ModelBounds(0, 40, 0, 20, 0, 10))
    limits = []
    dialog.offset_spin = SimpleNamespace(
        setRange=lambda low, high: limits.append((low, high)),
        setMinimum=lambda value: None, setMaximum=lambda value: None,
    )
    dialog.offset_slider = SimpleNamespace(setRange=lambda *args: None)
    dialog._sync_offset_limits()
    assert limits == [(-5., 5.)]


def test_section_dialog_slider_failure_does_not_disable_further_input(monkeypatch):
    dialog = _section_dialog_without_gui(monkeypatch)
    dialog._updating = False
    dialog._slider_to_offset = lambda value: 2.
    dialog.offset_spin = SimpleNamespace(setValue=lambda value: None)
    def fail(**kwargs):
        raise RuntimeError("plane update failed")
    monkeypatch.setattr(section, "configure_section_view", fail)
    with pytest.raises(RuntimeError, match="plane update failed"):
        dialog._offset_slider_changed(10)
    assert not dialog._updating


def test_section_state_query_handles_a_deleted_view_wrapper():
    assert not section.is_section_view_active(_DeletedSectionView())


@pytest.mark.parametrize("active", [None, object()])
def test_dragger_poll_does_not_touch_nodes_without_its_own_active_view(monkeypatch, active):
    monkeypatch.setattr(section, "_dragger_node", object())
    monkeypatch.setattr(section, "_dragger_view", object())
    monkeypatch.setattr(section, "_active_3d_view", lambda: active)
    monkeypatch.setattr(section, "_current_dragger_origin", lambda: pytest.fail("stale scene access"))
    section._poll_dragger()
