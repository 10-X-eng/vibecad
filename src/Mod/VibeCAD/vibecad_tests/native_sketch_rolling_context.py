# SPDX-License-Identifier: LGPL-2.1-or-later

"""Small edit-context transitions shared by the rolling Native Sketch GUI gate."""

from __future__ import annotations

import FreeCADGui as Gui

from VibeCADRibbonSurface import read_active_ribbon_surface
from vibecad_tests.native_sketch_geometry_gui_support import (
    edit_boundary,
    process_events,
)


_LABELS = {
    "EqualSketch": "Native Equal lifecycle",
    "SymmetricSketch": "Native Symmetric lifecycle",
    "BlockSketch": "Native Block lifecycle",
    "GroupSketch": "Native Constraint Group lifecycle",
    "DrivingSketch": "Native Driving Reference lifecycle",
    "ActiveSketch": "Native Active Inactive lifecycle",
    "FilletSketch": "Native Fillet lifecycle",
    "ChamferSketch": "Native Chamfer lifecycle",
    "TrimSketch": "Native Trim lifecycle",
    "SplitSketch": "Native Split lifecycle",
    "ExtendSketch": "Native Extend lifecycle",
    "DeleteGeometrySketch": "Native Delete Geometry lifecycle",
    "ProjectionSketch": "Native Projection lifecycle",
    "IntersectionSketch": "Native Intersection lifecycle",
    "CarbonCopySketch": "Native Carbon Copy lifecycle",
    "TranslateSketch": "Native Translate lifecycle",
    "RotateSketch": "Native Rotate lifecycle",
    "ScaleSketch": "Native Scale lifecycle",
    "OffsetSketch": "Native Offset lifecycle",
    "SymmetrySketch": "Native Symmetry lifecycle",
    "AxisAlignmentSketch": "Native Remove Axes Alignment lifecycle",
    "NURBSConversionSketch": "Native Geometry to B-Spline lifecycle",
    "BSplineDegreeSketch": "Native B-spline degree lifecycle",
    "BSplineDegreeDecreaseSketch": "Native B-spline degree reduction lifecycle",
    "BSplineKnotMultiplicityIncreaseSketch": (
        "Native B-spline knot multiplicity lifecycle"
    ),
    "BSplineKnotMultiplicityDecreaseSketch": (
        "Native B-spline knot multiplicity decrease lifecycle"
    ),
    "BSplineKnotInsertionSketch": "Native B-spline knot insertion lifecycle",
    "JoinSketch": "Native Join Curves lifecycle",
    "InspectSketch": "Native constraint relationship read lifecycle",
    "ArcOverlaySketch": "Native circular arc helper presentation lifecycle",
    "BSplineDegreeViewSketch": (
        "Native B-spline degree-information presentation lifecycle"
    ),
    "BSplineControlPolygonViewSketch": (
        "Native B-spline control-polygon presentation lifecycle"
    ),
    "BSplineCurvatureCombViewSketch": (
        "Native B-spline curvature-comb presentation lifecycle"
    ),
    "BSplineKnotMultiplicityViewSketch": (
        "Native B-spline knot-label presentation lifecycle"
    ),
    "BSplinePoleWeightViewSketch": (
        "Native B-spline pole-weight presentation lifecycle"
    ),
    "InternalAlignmentSketch": "Native internal-alignment geometry lifecycle",
    "VirtualSpaceSketch": "Native virtual-space lifecycle",
    "ViewActionsSketch": "Native Sketch view-action lifecycle",
}


def open_separate_sketch_case(
    document,
    controller,
    name: str,
):
    Gui.activeDocument().resetEdit()
    process_events(16)
    sketch = document.addObject("Sketcher::SketchObject", name)
    sketch.Label = _LABELS[name]
    document.recompute()
    assert Gui.activeDocument().setEdit(sketch.Name)
    process_events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "sketch.edit"
    return sketch, surface, edit_boundary(document, sketch, controller)


def reopen_separate_sketch_case(
    document,
    controller,
    name: str,
):
    Gui.activeDocument().resetEdit()
    process_events(16)
    sketch = document.getObject(name)
    assert sketch is not None
    assert Gui.activeDocument().setEdit(sketch.Name)
    process_events(24)
    assert read_active_ribbon_surface(controller).surface_id == "sketch.edit"
    return sketch
