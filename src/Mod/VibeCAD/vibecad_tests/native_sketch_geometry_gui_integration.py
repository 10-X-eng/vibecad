# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real GUI lifecycle gate for Native geometry in a human-opened Sketch."""

from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part  # noqa: F401 - registers Point geometry
from PySide import QtCore, QtWidgets
import Sketcher  # noqa: F401 - registers Sketcher object types

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADEditState import active_edit_object
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSketchConstraintBindings import SKETCH_CONSTRAINT_CAPABILITY_NAME
from VibeCADNativeSketchGeometryBindings import SKETCH_GEOMETRY_CAPABILITY_NAME
from VibeCADNativeSketchInspectBindings import SKETCH_INSPECT_CAPABILITY_NAME
from VibeCADNativeSketchPresentationBindings import (
    SKETCH_PRESENTATION_CAPABILITY_NAME,
)
from VibeCADNativeSketchState import (
    serialize_sketch_geometry,
)
from VibeCADNativeSurface import (
    NativeSurfaceSnapshot,
    require_frozen_native_surface,
)
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from vibecad_tests.native_sketch_basic_geometry_gui_case import (
    exercise_basic_geometry_case,
)
from vibecad_tests.native_sketch_angle_gui_case import exercise_angle_case
from vibecad_tests.native_sketch_coincident_gui_case import exercise_coincident_case
from vibecad_tests.native_sketch_horizontal_vertical_gui_case import (
    exercise_horizontal_vertical_case,
)
from vibecad_tests.native_sketch_horizontal_gui_case import (
    exercise_horizontal_case,
)
from vibecad_tests.native_sketch_vertical_gui_case import exercise_vertical_case
from vibecad_tests.native_sketch_parallel_gui_case import exercise_parallel_case
from vibecad_tests.native_sketch_perpendicular_gui_case import (
    exercise_perpendicular_case,
)
from vibecad_tests.native_sketch_tangent_gui_case import exercise_tangent_case
from vibecad_tests.native_sketch_gui_constraint_operations import (
    ROLLING_SKETCH_OPERATION_NAMES,
    SKETCH_CONSTRAINT_OPERATIONS,
    SKETCH_INSPECT_OPERATIONS,
    SKETCH_PRESENTATION_OPERATIONS,
)
from vibecad_tests.native_sketch_separate_cases import (
    exercise_separate_sketch_cases,
)
from vibecad_tests.native_sketch_lock_gui_case import exercise_lock_case
from vibecad_tests.native_sketch_dimension_gui_case import exercise_dimension_case
from vibecad_tests.native_sketch_diameter_gui_case import exercise_diameter_case
from vibecad_tests.native_sketch_distance_gui_case import exercise_distance_case
from vibecad_tests.native_sketch_distance_x_gui_case import (
    exercise_horizontal_distance_case,
)
from vibecad_tests.native_sketch_distance_y_gui_case import (
    exercise_vertical_distance_case,
)
from vibecad_tests.native_sketch_geometry_catalog_gui_case import (
    exercise_catalog_geometry_cases,
)
from vibecad_tests.native_sketch_rolling_reopen import verify_rolling_reopen
from vibecad_tests.native_sketch_radiam_gui_case import exercise_radiam_case
from vibecad_tests.native_sketch_radius_gui_case import exercise_radius_case
from vibecad_tests.native_sketch_geometry_gui_support import (
    arc_arguments,
    edit_boundary as _edit_boundary,
    elliptical_arc_arguments,
    hyperbolic_arc_arguments,
    parabolic_arc_arguments,
    process_events as _process_events,
    provider_turn,
    three_point_arc_arguments,
)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeSketchGeometryGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        sketch = document.addObject("Sketcher::SketchObject", "Sketch")
        sketch.Label = "Native Point Target"
        other = document.addObject("Sketcher::SketchObject", "OtherSketch")
        other.Label = "Non-active Sketch"
        external_source = document.addObject("Part::Feature", "ExternalSource")
        external_source.Label = "Construction external source"
        external_source.Shape = Part.makeCompound(
            [
                Part.makeLine(
                    App.Vector(-20.0, 30.0, 0.0),
                    App.Vector(20.0, 30.0, 0.0),
                ),
                Part.makeLine(
                    App.Vector(-20.0, 40.0, 0.0),
                    App.Vector(20.0, 40.0, 0.0),
                ),
            ]
        )
        intersection_source = document.addObject("Part::Feature", "IntersectionSource")
        intersection_source.Label = "Crossing intersection source"
        intersection_source.Shape = Part.makeLine(
            App.Vector(-12.0, 55.0, -8.0),
            App.Vector(12.0, 55.0, 8.0),
        )
        carbon_support = document.addObject("Part::Feature", "CarbonCopySupport")
        carbon_support.Label = "Carbon Copy source support"
        carbon_support.Shape = Part.makeLine(
            App.Vector(-8.0, 65.0, 0.0),
            App.Vector(8.0, 65.0, 0.0),
        )
        carbon_source = document.addObject(
            "Sketcher::SketchObject",
            "CarbonCopySource",
        )
        carbon_source.Label = "Exact Carbon Copy source"
        carbon_source.addGeometry(
            Part.LineSegment(
                App.Vector(-5.0, 2.0, 0.0),
                App.Vector(5.0, 2.0, 0.0),
            ),
            False,
        )
        carbon_source.addConstraint(Sketcher.Constraint("Distance", 0, 10.0))
        carbon_source.addExternal(carbon_support.Name, "Edge1", False, False)
        document.recompute()
        sketch.addExternal(external_source.Name, "Edge1")
        document.recompute()
        document.clearUndos()
        _process_events()

        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        assert read_active_ribbon_surface(controller).surface_id == "model"

        assert Gui.activeDocument().setEdit(sketch.Name)
        _process_events(32)
        live_surface = read_active_ribbon_surface(controller)
        assert live_surface.surface_id == "sketch.edit"
        assert active_edit_object() is sketch
        boundary = _edit_boundary(document, sketch, controller)
        active_call_state = {
            "frozen_surface": NativeSurfaceSnapshot.from_surface(live_surface),
            "sketch": sketch,
            "boundary": boundary,
        }
        assert boundary[4:] == (0, False)
        for action_id in (
            "Sketcher_Translate",
            "Sketcher_Rotate",
            "Sketcher_Scale",
            "Sketcher_Offset",
            "Sketcher_Symmetry",
            "Sketcher_RemoveAxesAlignment",
            "Sketcher_BSplineConvertToNURBS",
            "Sketcher_BSplineIncreaseDegree",
            "Sketcher_BSplineDecreaseDegree",
            "Sketcher_BSplineIncreaseKnotMultiplicity",
            "Sketcher_BSplineInsertKnot",
        ):
            assert action_id in live_surface.command_ids

        production = resolve_native_provider_surface(
            live_surface,
            build_native_capability_registry(),
        )
        assert production.available is True
        assert production.missing_action_ids == ()
        assert production.missing_definition_names == ()
        assert production.missing_implementation_names == ()
        assert production.incomplete_definition_names == ()
        assert set(production.tool_names) >= {
            SKETCH_GEOMETRY_CAPABILITY_NAME,
            SKETCH_CONSTRAINT_CAPABILITY_NAME,
            SKETCH_INSPECT_CAPABILITY_NAME,
            SKETCH_PRESENTATION_CAPABILITY_NAME,
        }
        assert production.schemas

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-sketch-geometry-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(
                active_call_state["frozen_surface"],
                controller,
            )

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: active_edit_object() is not None,
        )

        def dispatcher_for_surface(surface):
            scoped_turn = provider_turn(surface)
            return NativeTurnDispatcher(
                document=document,
                state=state,
                registry=build_native_capability_registry(),
                turn=scoped_turn,
                runtimes=build_native_runtime_bindings(context, scoped_turn.tool_names),
                reauthorize_turn=reauthorize,
                active_document=lambda: App.ActiveDocument,
            )

        active_call_state["dispatcher"] = dispatcher_for_surface(live_surface)
        call_number = 0

        def native_call(arguments, *, succeeds=True, call_id=None):
            nonlocal call_number
            call_number += 1
            operation = arguments.get("operation")
            if operation in SKETCH_INSPECT_OPERATIONS:
                tool_name = SKETCH_INSPECT_CAPABILITY_NAME
            elif operation in SKETCH_PRESENTATION_OPERATIONS:
                tool_name = SKETCH_PRESENTATION_CAPABILITY_NAME
            elif operation in SKETCH_CONSTRAINT_OPERATIONS:
                tool_name = SKETCH_CONSTRAINT_CAPABILITY_NAME
            else:
                tool_name = SKETCH_GEOMETRY_CAPABILITY_NAME
            response = active_call_state["dispatcher"].call(
                tool_name,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"sketch-geometry-call-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            assert (
                _edit_boundary(
                    document,
                    active_call_state["sketch"],
                    controller,
                )
                == (active_call_state["boundary"])
            )
            return response

        exercise_basic_geometry_case(
            sketch=sketch,
            other_sketch=other,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )

        arc_undo_before = int(document.UndoCount)
        invalid_arc = arc_arguments(
            sketch,
            geometry_count=5,
            center=(18.0, 12.0),
            radius=0.0,
            start_degrees=30.0,
            sweep_degrees=120.0,
        )
        arc_failure = native_call(invalid_arc, succeeds=False)
        assert arc_failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert int(document.UndoCount) == arc_undo_before
        assert int(sketch.GeometryCount) == 5
        assert int(sketch.ConstraintCount) == 2

        arc_arguments_payload = arc_arguments(
            sketch,
            geometry_count=5,
            center=(18.0, 12.0),
            radius=6.0,
            start_degrees=30.0,
            sweep_degrees=120.0,
        )
        arc_response = native_call(arc_arguments_payload)
        assert arc_response["geometry_count"] == 6
        assert arc_response["constraint_count"] == 2
        arc_geometry = arc_response["geometry"]
        assert arc_geometry["index"] == 5
        assert arc_geometry["type_id"] == "Part::GeomArcOfCircle"
        assert arc_geometry["kind"] == "circular_arc"
        assert arc_geometry["construction"] is False
        assert arc_geometry["center_mm"] == [18.0, 12.0, 0.0]
        assert arc_geometry["axis"] == [0.0, 0.0, 1.0]
        assert arc_geometry["radius_mm"] == 6.0
        assert math.isclose(
            arc_geometry["first_parameter"],
            math.radians(30.0),
            abs_tol=1.0e-10,
        )
        assert math.isclose(
            arc_geometry["last_parameter"],
            math.radians(150.0),
            abs_tol=1.0e-10,
        )
        assert arc_response["assistant_undo_available"] is True
        assert len(arc_response["receipt"]["changed"]) == 1
        assert int(document.UndoCount) == arc_undo_before + 1
        assert document.UndoNames[0] == "Create Native Sketch Arc"

        document.undo()
        _process_events(16)
        assert active_edit_object() is sketch
        assert int(sketch.GeometryCount) == 5
        assert int(sketch.ConstraintCount) == 2
        document.redo()
        _process_events(16)
        assert active_edit_object() is sketch
        assert int(sketch.GeometryCount) == 6
        assert int(sketch.ConstraintCount) == 2
        assert serialize_sketch_geometry(sketch, 5) == arc_geometry
        assert _edit_boundary(document, sketch, controller) == boundary

        three_point_undo_before = int(document.UndoCount)
        collinear_arc = three_point_arc_arguments(
            sketch,
            geometry_count=6,
            first=(-15.0, 10.0),
            second=(-5.0, 10.0),
            rim=(-10.0, 10.0),
        )
        collinear_failure = native_call(collinear_arc, succeeds=False)
        assert collinear_failure["error_code"] == "NATIVE_SKETCH_INVALID"
        assert int(document.UndoCount) == three_point_undo_before
        assert int(sketch.GeometryCount) == 6
        assert int(sketch.ConstraintCount) == 2

        three_point_arguments = three_point_arc_arguments(
            sketch,
            geometry_count=6,
            first=(-15.0, 10.0),
            second=(-5.0, 10.0),
            rim=(-10.0, 5.0),
        )
        three_point_response = native_call(three_point_arguments)
        assert three_point_response["geometry_count"] == 7
        assert three_point_response["constraint_count"] == 2
        three_point_geometry = three_point_response["geometry"]
        assert three_point_geometry["index"] == 6
        assert three_point_geometry["type_id"] == "Part::GeomArcOfCircle"
        assert three_point_geometry["kind"] == "circular_arc"
        assert three_point_geometry["construction"] is False
        assert three_point_geometry["center_mm"] == [-10.0, 10.0, 0.0]
        assert three_point_geometry["axis"] == [0.0, 0.0, 1.0]
        assert three_point_geometry["radius_mm"] == 5.0
        assert math.isclose(
            three_point_geometry["first_parameter"],
            math.pi,
            abs_tol=1.0e-10,
        )
        assert math.isclose(
            three_point_geometry["last_parameter"],
            math.tau,
            abs_tol=1.0e-10,
        )
        assert three_point_geometry["start_mm"] == [-15.0, 10.0, 0.0]
        assert math.isclose(
            three_point_geometry["end_mm"][0],
            -5.0,
            abs_tol=1.0e-10,
        )
        assert math.isclose(
            three_point_geometry["end_mm"][1],
            10.0,
            abs_tol=1.0e-10,
        )
        assert int(document.UndoCount) == three_point_undo_before + 1
        assert document.UndoNames[0] == "Create Native Sketch Three-Point Arc"

        document.undo()
        _process_events(16)
        assert active_edit_object() is sketch
        assert int(sketch.GeometryCount) == 6
        assert int(sketch.ConstraintCount) == 2
        document.redo()
        _process_events(16)
        assert active_edit_object() is sketch
        assert int(sketch.GeometryCount) == 7
        assert int(sketch.ConstraintCount) == 2
        assert serialize_sketch_geometry(sketch, 6) == three_point_geometry
        assert _edit_boundary(document, sketch, controller) == boundary

        elliptical_undo_before = int(document.UndoCount)
        degenerate_ellipse = elliptical_arc_arguments(
            sketch,
            geometry_count=7,
            center=(0.0, -15.0),
            major_radius=8.0,
            minor_radius=8.0,
            rotation_degrees=30.0,
            start_degrees=20.0,
            sweep_degrees=130.0,
        )
        elliptical_failure = native_call(degenerate_ellipse, succeeds=False)
        assert elliptical_failure["error_code"] == "NATIVE_SKETCH_INVALID"
        assert int(document.UndoCount) == elliptical_undo_before
        assert int(sketch.GeometryCount) == 7
        assert int(sketch.ConstraintCount) == 2

        elliptical_arguments = elliptical_arc_arguments(
            sketch,
            geometry_count=7,
            center=(0.0, -15.0),
            major_radius=8.0,
            minor_radius=3.0,
            rotation_degrees=30.0,
            start_degrees=20.0,
            sweep_degrees=130.0,
        )
        elliptical_response = native_call(elliptical_arguments)
        assert elliptical_response["geometry_count"] == 12
        assert elliptical_response["constraint_count"] == 6
        elliptical_geometry = elliptical_response["geometry"]
        assert elliptical_geometry["index"] == 7
        assert elliptical_geometry["type_id"] == "Part::GeomArcOfEllipse"
        assert elliptical_geometry["kind"] == "elliptical_arc"
        assert elliptical_geometry["construction"] is False
        assert elliptical_geometry["center_mm"] == [0.0, -15.0, 0.0]
        assert elliptical_geometry["axis"] == [0.0, 0.0, 1.0]
        assert math.isclose(
            elliptical_geometry["x_axis"][0],
            math.cos(math.radians(30.0)),
            abs_tol=1.0e-10,
        )
        assert math.isclose(
            elliptical_geometry["x_axis"][1],
            math.sin(math.radians(30.0)),
            abs_tol=1.0e-10,
        )
        assert elliptical_geometry["major_radius_mm"] == 8.0
        assert elliptical_geometry["minor_radius_mm"] == 3.0
        assert math.isclose(
            elliptical_geometry["first_parameter"],
            math.radians(20.0),
            abs_tol=1.0e-10,
        )
        assert math.isclose(
            elliptical_geometry["last_parameter"],
            math.radians(150.0),
            abs_tol=1.0e-10,
        )
        elliptical_internal = elliptical_response["internal_geometries"]
        assert [item["index"] for item in elliptical_internal] == [8, 9, 10, 11]
        assert [item["internal_type"] for item in elliptical_internal] == [
            "EllipseMajorDiameter",
            "EllipseMinorDiameter",
            "EllipseFocus1",
            "EllipseFocus2",
        ]
        assert [item["kind"] for item in elliptical_internal] == [
            "line",
            "line",
            "point",
            "point",
        ]
        assert all(item["construction"] is True for item in elliptical_internal)
        elliptical_constraints = elliptical_response["internal_constraints"]
        assert [item["index"] for item in elliptical_constraints] == [2, 3, 4, 5]
        assert [item["references"] for item in elliptical_constraints] == [
            [
                {"slot": 1, "geometry_index": 8},
                {"slot": 2, "geometry_index": 7},
            ],
            [
                {"slot": 1, "geometry_index": 9},
                {"slot": 2, "geometry_index": 7},
            ],
            [
                {"slot": 1, "geometry_index": 10, "position": 1},
                {"slot": 2, "geometry_index": 7},
            ],
            [
                {"slot": 1, "geometry_index": 11, "position": 1},
                {"slot": 2, "geometry_index": 7},
            ],
        ]
        assert elliptical_response["assistant_undo_available"] is True
        assert len(elliptical_response["receipt"]["changed"]) == 1
        assert int(document.UndoCount) == elliptical_undo_before + 1
        assert document.UndoNames[0] == "Create Native Sketch Elliptical Arc"

        document.undo()
        _process_events(16)
        assert active_edit_object() is sketch
        assert int(sketch.GeometryCount) == 7
        assert int(sketch.ConstraintCount) == 2
        document.redo()
        _process_events(16)
        assert active_edit_object() is sketch
        assert int(sketch.GeometryCount) == 12
        assert int(sketch.ConstraintCount) == 6
        assert serialize_sketch_geometry(sketch, 7) == elliptical_geometry
        assert _edit_boundary(document, sketch, controller) == boundary

        hyperbolic_undo_before = int(document.UndoCount)
        degenerate_hyperbola = hyperbolic_arc_arguments(
            sketch,
            geometry_count=12,
            center=(15.0, -12.0),
            major_radius=5.0,
            minor_radius=3.0,
            rotation_degrees=20.0,
            start_parameter=1.0,
            end_parameter=1.0,
        )
        hyperbolic_failure = native_call(degenerate_hyperbola, succeeds=False)
        assert hyperbolic_failure["error_code"] == "NATIVE_SKETCH_INVALID"
        assert int(document.UndoCount) == hyperbolic_undo_before
        assert int(sketch.GeometryCount) == 12
        assert int(sketch.ConstraintCount) == 6

        hyperbolic_arguments = hyperbolic_arc_arguments(
            sketch,
            geometry_count=12,
            center=(15.0, -12.0),
            major_radius=5.0,
            minor_radius=3.0,
            rotation_degrees=20.0,
            start_parameter=-1.0,
            end_parameter=1.0,
        )
        hyperbolic_response = native_call(hyperbolic_arguments)
        assert hyperbolic_response["geometry_count"] == 16
        assert hyperbolic_response["constraint_count"] == 9
        hyperbolic_geometry = hyperbolic_response["geometry"]
        assert hyperbolic_geometry["index"] == 12
        assert hyperbolic_geometry["type_id"] == "Part::GeomArcOfHyperbola"
        assert hyperbolic_geometry["kind"] == "hyperbolic_arc"
        assert hyperbolic_geometry["construction"] is False
        assert hyperbolic_geometry["center_mm"] == [15.0, -12.0, 0.0]
        assert hyperbolic_geometry["axis"] == [0.0, 0.0, 1.0]
        assert math.isclose(
            hyperbolic_geometry["x_axis"][0],
            math.cos(math.radians(20.0)),
            abs_tol=1.0e-10,
        )
        assert math.isclose(
            hyperbolic_geometry["x_axis"][1],
            math.sin(math.radians(20.0)),
            abs_tol=1.0e-10,
        )
        assert hyperbolic_geometry["major_radius_mm"] == 5.0
        assert hyperbolic_geometry["minor_radius_mm"] == 3.0
        assert hyperbolic_geometry["first_parameter"] == -1.0
        assert hyperbolic_geometry["last_parameter"] == 1.0
        hyperbolic_internal = hyperbolic_response["internal_geometries"]
        assert [item["index"] for item in hyperbolic_internal] == [13, 14, 15]
        assert [item["internal_type"] for item in hyperbolic_internal] == [
            "HyperbolaMajor",
            "HyperbolaMinor",
            "HyperbolaFocus",
        ]
        assert [item["kind"] for item in hyperbolic_internal] == [
            "line",
            "line",
            "point",
        ]
        assert all(item["construction"] is True for item in hyperbolic_internal)
        hyperbolic_constraints = hyperbolic_response["internal_constraints"]
        assert [item["index"] for item in hyperbolic_constraints] == [6, 7, 8]
        assert [item["references"] for item in hyperbolic_constraints] == [
            [
                {"slot": 1, "geometry_index": 13},
                {"slot": 2, "geometry_index": 12},
            ],
            [
                {"slot": 1, "geometry_index": 14},
                {"slot": 2, "geometry_index": 12},
            ],
            [
                {"slot": 1, "geometry_index": 15, "position": 1},
                {"slot": 2, "geometry_index": 12},
            ],
        ]
        assert hyperbolic_response["assistant_undo_available"] is True
        assert len(hyperbolic_response["receipt"]["changed"]) == 1
        assert int(document.UndoCount) == hyperbolic_undo_before + 1
        assert document.UndoNames[0] == "Create Native Sketch Hyperbolic Arc"

        document.undo()
        _process_events(16)
        assert active_edit_object() is sketch
        assert int(sketch.GeometryCount) == 12
        assert int(sketch.ConstraintCount) == 6
        document.redo()
        _process_events(16)
        assert active_edit_object() is sketch
        assert int(sketch.GeometryCount) == 16
        assert int(sketch.ConstraintCount) == 9
        assert serialize_sketch_geometry(sketch, 12) == hyperbolic_geometry
        assert _edit_boundary(document, sketch, controller) == boundary

        parabolic_undo_before = int(document.UndoCount)
        degenerate_parabola = parabolic_arc_arguments(
            sketch,
            geometry_count=16,
            vertex=(-18.0, -10.0),
            focal_length=4.0,
            rotation_degrees=35.0,
            start_parameter=2.0,
            end_parameter=2.0,
        )
        parabolic_failure = native_call(degenerate_parabola, succeeds=False)
        assert parabolic_failure["error_code"] == "NATIVE_SKETCH_INVALID"
        assert int(document.UndoCount) == parabolic_undo_before
        assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (16, 9)

        parabolic_arguments = parabolic_arc_arguments(
            sketch,
            geometry_count=16,
            vertex=(-18.0, -10.0),
            focal_length=4.0,
            rotation_degrees=35.0,
            start_parameter=-5.0,
            end_parameter=6.0,
        )
        parabolic_response = native_call(parabolic_arguments)
        assert (
            parabolic_response["geometry_count"],
            parabolic_response["constraint_count"],
        ) == (19, 11)
        parabolic_geometry = parabolic_response["geometry"]
        assert parabolic_geometry["index"] == 16
        assert parabolic_geometry["type_id"] == "Part::GeomArcOfParabola"
        assert parabolic_geometry["kind"] == "parabolic_arc"
        assert parabolic_geometry["construction"] is False
        assert parabolic_geometry["center_mm"] == [-18.0, -10.0, 0.0]
        assert parabolic_geometry["axis"] == [0.0, 0.0, 1.0]
        assert math.isclose(
            parabolic_geometry["x_axis"][0],
            math.cos(math.radians(35.0)),
            abs_tol=1.0e-10,
        )
        assert math.isclose(
            parabolic_geometry["x_axis"][1],
            math.sin(math.radians(35.0)),
            abs_tol=1.0e-10,
        )
        assert parabolic_geometry["focal_length_mm"] == 4.0
        assert parabolic_geometry["first_parameter"] == -5.0
        assert parabolic_geometry["last_parameter"] == 6.0
        parabolic_internal = parabolic_response["internal_geometries"]
        assert [item["index"] for item in parabolic_internal] == [17, 18]
        assert [item["internal_type"] for item in parabolic_internal] == [
            "ParabolaFocus",
            "ParabolaFocalAxis",
        ]
        assert [item["kind"] for item in parabolic_internal] == ["point", "line"]
        assert all(item["construction"] is True for item in parabolic_internal)
        parabolic_constraints = parabolic_response["internal_constraints"]
        assert [item["index"] for item in parabolic_constraints] == [9, 10]
        assert [item["references"] for item in parabolic_constraints] == [
            [
                {"slot": 1, "geometry_index": 17, "position": 1},
                {"slot": 2, "geometry_index": 16},
            ],
            [
                {"slot": 1, "geometry_index": 18},
                {"slot": 2, "geometry_index": 16},
            ],
        ]
        assert int(document.UndoCount) == parabolic_undo_before + 1
        assert document.UndoNames[0] == "Create Native Sketch Parabolic Arc"

        document.undo()
        _process_events(16)
        assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (16, 9)
        document.redo()
        _process_events(16)
        assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (19, 11)
        assert serialize_sketch_geometry(sketch, 16) == parabolic_geometry
        assert _edit_boundary(document, sketch, controller) == boundary

        catalog_state = exercise_catalog_geometry_cases(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        dimension_state = exercise_dimension_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        horizontal_distance_state = exercise_horizontal_distance_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        vertical_distance_state = exercise_vertical_distance_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        distance_state = exercise_distance_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        radiam_state = exercise_radiam_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        radius_state = exercise_radius_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        diameter_state = exercise_diameter_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        angle_state = exercise_angle_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        lock_state = exercise_lock_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        coincident_state = exercise_coincident_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        horizontal_vertical_state = exercise_horizontal_vertical_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        horizontal_state = exercise_horizontal_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        vertical_state = exercise_vertical_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        parallel_state = exercise_parallel_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        perpendicular_state = exercise_perpendicular_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        tangent_state = exercise_tangent_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
            boundary=boundary,
            controller=controller,
        )
        separate_states = exercise_separate_sketch_cases(
            document=document,
            controller=controller,
            active_call_state=active_call_state,
            dispatcher_for_surface=dispatcher_for_surface,
            native_call=native_call,
            process_events=_process_events,
            edit_boundary=_edit_boundary,
        )
        reopen_state = {
            "elliptical_constraints": elliptical_constraints,
            "hyperbolic_constraints": hyperbolic_constraints,
            "parabolic_constraints": parabolic_constraints,
            **catalog_state,
            "dimension": dimension_state,
            "horizontal_distance": horizontal_distance_state,
            "vertical_distance": vertical_distance_state,
            "distance": distance_state,
            "radiam": radiam_state,
            "radius": radius_state,
            "diameter": diameter_state,
            "angle": angle_state,
            "lock": lock_state,
            "coincident": coincident_state,
            "horizontal_vertical": horizontal_vertical_state,
            "horizontal": horizontal_state,
            "vertical": vertical_state,
            "parallel": parallel_state,
            "perpendicular": perpendicular_state,
            "tangent": tangent_state,
        }

        Gui.activeDocument().resetEdit()
        _process_events(16)
        save_path = Path(tempfile.mkdtemp(prefix="vibecad-native-sketch-geometry-")) / (
            "NativeSketchGeometry.FCStd"
        )
        document.saveAs(str(save_path))
        saved_name = document.Name
        sketch_name = sketch.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        VibeGui._connect_document_observer()
        _process_events(16)

        verify_rolling_reopen(
            document,
            controller,
            main_sketch_name=sketch_name,
            main_state=reopen_state,
            separate_states=separate_states,
        )

        print(
            "VIBECAD_NATIVE_SKETCH_GEOMETRY_GUI_OK "
            "operations=" + ",".join(ROLLING_SKETCH_OPERATION_NAMES),
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=__import__("sys").__stderr__)
    finally:
        if Gui.activeDocument() and Gui.activeDocument().getInEdit():
            Gui.activeDocument().resetEdit()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
