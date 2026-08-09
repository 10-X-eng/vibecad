# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared real-GUI state helpers for exact Sketch presentation operations."""

from __future__ import annotations

from typing import Any

import FreeCAD as App
import FreeCADGui as Gui
from pivy import coin

from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchMutationState import geometry_records_without_tags
from VibeCADNativeSketchPresentationState import SKETCH_GENERAL_PREFERENCES
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
)


def preference_snapshot(key: str, default: bool) -> tuple[bool, bool]:
    group = App.ParamGet(SKETCH_GENERAL_PREFERENCES)
    present = key in tuple(group.GetBools())
    return present, bool(group.GetBool(key, default))


def restore_preference(key: str, snapshot: tuple[bool, bool]) -> None:
    present, visible = snapshot
    group = App.ParamGet(SKETCH_GENERAL_PREFERENCES)
    if present:
        group.SetBool(key, visible)
    else:
        group.RemBool(key)


def sketch_presentation_model_state(sketch: Any) -> dict[str, Any]:
    return {
        "geometry": geometry_records_without_tags(
            canonical_sketch_records(iter_sketch_geometry_records(sketch))
        ),
        "constraints": canonical_sketch_records(iter_sketch_constraint_records(sketch)),
        "external_geometry": canonical_sketch_records(
            iter_sketch_external_geometry_records(sketch)
        ),
        "expressions": tuple(
            (str(path), str(expression)) for path, expression in sketch.ExpressionEngine
        ),
        "degrees_of_freedom": int(sketch.DoF),
    }


def information_group() -> Any:
    assert Gui.activeDocument().getInEdit() is not None
    search = coin.SoSearchAction()
    search.setName("InformationGroup")
    search.setInterest(coin.SoSearchAction.ALL)
    search.setSearchingAll(True)
    search.apply(Gui.activeDocument().activeView().getSceneGraph())
    paths = search.getPaths()
    assert paths.getLength() == 1, paths.getLength()
    return coin.cast(paths[0].getTail(), "SoGroup")


def bspline_information_switches(
    sketch: Any, spline_index: int
) -> dict[str, tuple[Any, ...]]:
    """Resolve the five renderer-defined layers for one sole root B-spline."""

    roots = [
        index
        for index, geometry in enumerate(sketch.Geometry)
        if geometry.TypeId == "Part::GeomBSplineCurve"
    ]
    assert roots == [spline_index], roots
    spline = sketch.Geometry[spline_index]
    knot_count = int(spline.NbKnots)
    pole_count = int(spline.NbPoles)
    group = information_group()
    expected = 3 + knot_count + pole_count
    assert int(group.getNumChildren()) == expected, (
        int(group.getNumChildren()),
        expected,
    )
    nodes = tuple(group.getChild(index) for index in range(expected))
    return {
        "degree": nodes[0:1],
        "control_polygon": nodes[1:2],
        "curvature_comb": nodes[2:3],
        "knot_multiplicity": nodes[3 : 3 + knot_count],
        "pole_weight": nodes[3 + knot_count :],
    }


def switch_states(nodes: tuple[Any, ...]) -> tuple[int, ...]:
    return tuple(int(node.whichChild.getValue()) for node in nodes)


def text_switch_values(nodes: tuple[Any, ...]) -> tuple[tuple[str, ...], ...]:
    values = []
    for node in nodes:
        separator = coin.cast(node.getChild(0), "SoSeparator")
        text = coin.cast(separator.getChild(3), "SoText2")
        values.append(tuple(str(value) for value in text.string.getValues()))
    return tuple(values)


def text_switch_translations(
    nodes: tuple[Any, ...],
) -> tuple[tuple[float, float, float], ...]:
    values = []
    for node in nodes:
        separator = coin.cast(node.getChild(0), "SoSeparator")
        translation = coin.cast(separator.getChild(2), "SoTranslation")
        values.append(
            tuple(float(component) for component in translation.translation.getValue())
        )
    return tuple(values)


def polygon_switch_geometry(
    nodes: tuple[Any, ...],
) -> tuple[dict[str, tuple[Any, ...]], ...]:
    """Read the actual coordinate and line topology under polygon switches."""

    values = []
    for node in nodes:
        separator = coin.cast(node.getChild(0), "SoSeparator")
        coordinates = coin.cast(separator.getChild(1), "SoCoordinate3")
        lines = coin.cast(separator.getChild(2), "SoLineSet")
        points = tuple(
            tuple(float(component) for component in point.getValue())
            for point in coordinates.point.getValues()
        )
        values.append(
            {
                "points": points,
                "vertex_counts": tuple(
                    int(count) for count in lines.numVertices.getValues()
                ),
            }
        )
    return tuple(values)
