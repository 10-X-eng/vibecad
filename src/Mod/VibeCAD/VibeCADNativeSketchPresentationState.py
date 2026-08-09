# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact document and preference state for Sketch presentation operations."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
)


SKETCH_GENERAL_PREFERENCES = "User parameter:BaseApp/Preferences/Mod/Sketcher/General"
ARC_OVERLAY_PREFERENCE = "ArcCircleHelperVisible"
BSPLINE_DEGREE_PREFERENCE = "BSplineDegreeVisible"
BSPLINE_CONTROL_POLYGON_PREFERENCE = "BSplineControlPolygonVisible"
BSPLINE_CURVATURE_COMB_PREFERENCE = "BSplineCombVisible"
BSPLINE_KNOT_MULTIPLICITY_PREFERENCE = "BSplineKnotMultiplicityVisible"
BSPLINE_POLE_WEIGHT_PREFERENCE = "BSplinePoleWeightVisible"


@dataclass(frozen=True, slots=True)
class FrozenSketchPresentationState:
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    external_geometry_records: tuple[str, ...]
    internal_arc_count: int
    external_arc_count: int
    internal_b_spline_count: int
    external_b_spline_count: int


def _application() -> Any:
    import FreeCAD as App

    return App


def _parameter_group(application: Any | None = None) -> Any:
    app = _application() if application is None else application
    try:
        group = app.ParamGet(SKETCH_GENERAL_PREFERENCES)
    except Exception as exc:
        raise NativeSketchError(
            "Sketch presentation preferences are unavailable."
        ) from exc
    if group is None:
        raise NativeSketchError("Sketch presentation preferences are unavailable.")
    return group


def read_sketch_presentation_visible(
    key: str,
    default_visible: bool,
    label: str,
    application: Any | None = None,
) -> bool:
    if not key or type(default_visible) is not bool or not label:
        raise TypeError("Sketch presentation preference metadata is invalid.")
    try:
        return bool(_parameter_group(application).GetBool(key, default_visible))
    except NativeSketchError:
        raise
    except Exception as exc:
        raise NativeSketchError(f"{label} is unavailable.") from exc


def write_sketch_presentation_visible(
    key: str,
    visible: bool,
    label: str,
    application: Any | None = None,
) -> None:
    if not key or type(visible) is not bool or not label:
        raise TypeError("Sketch presentation preference metadata is invalid.")
    try:
        _parameter_group(application).SetBool(key, visible)
    except NativeSketchError:
        raise
    except Exception as exc:
        raise NativeSketchError(f"{label} could not be changed.") from exc


def read_arc_overlay_visible(application: Any | None = None) -> bool:
    """Read the renderer's actual default, which is hidden when the key is absent."""

    return read_sketch_presentation_visible(
        ARC_OVERLAY_PREFERENCE,
        False,
        "Circular arc helper visibility",
        application,
    )


def write_arc_overlay_visible(
    visible: bool,
    application: Any | None = None,
) -> None:
    write_sketch_presentation_visible(
        ARC_OVERLAY_PREFERENCE,
        visible,
        "Circular arc helper visibility",
        application,
    )


def _kind_count(records: tuple[str, ...], kind: str) -> int:
    return sum(
        1 for encoded in records if str(json.loads(encoded).get("kind") or "") == kind
    )


def freeze_sketch_presentation_state(
    sketch: Any,
    *,
    expected_geometry_count: int,
    expected_constraint_count: int,
    expected_external_geometry_count: int,
) -> FrozenSketchPresentationState:
    try:
        geometry = canonical_sketch_records(
            iter_sketch_geometry_records(sketch, expected_geometry_count)
        )
        constraints = canonical_sketch_records(
            iter_sketch_constraint_records(sketch, expected_constraint_count)
        )
        external = canonical_sketch_records(
            iter_sketch_external_geometry_records(sketch)
        )
    except Exception as exc:
        raise NativeSketchError(
            "The active Sketch presentation state cannot be verified exactly."
        ) from exc
    if len(external) != expected_external_geometry_count:
        raise NativeSketchError(
            "The active Sketch external geometry count changed; "
            "read its current state and retry."
        )
    return FrozenSketchPresentationState(
        geometry,
        constraints,
        external,
        _kind_count(geometry, "circular_arc"),
        _kind_count(external, "circular_arc"),
        _kind_count(geometry, "b_spline"),
        _kind_count(external, "b_spline"),
    )
