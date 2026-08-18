# SPDX-License-Identifier: LGPL-2.1-or-later

"""Clean-profile lifecycle gate for the contextual Native Sketch state read."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign
from PySide import QtCore, QtWidgets
import Sketcher

import VibeCADGui as VibeGui
from VibeCADNativeCapabilityRegistry import (
    resolve_native_provider_surface,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeSnapshot import MAX_NATIVE_SNAPSHOT_BYTES, build_active_snapshot
from VibeCADRibbonSurface import read_active_ribbon_surface


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _rectangle() -> list:
    return [
        Part.LineSegment(App.Vector(0, 0, 0), App.Vector(20, 0, 0)),
        Part.LineSegment(App.Vector(20, 0, 0), App.Vector(20, 12, 0)),
        Part.LineSegment(App.Vector(20, 12, 0), App.Vector(0, 12, 0)),
        Part.LineSegment(App.Vector(0, 12, 0), App.Vector(0, 0, 0)),
    ]


def _build_sketch(document):
    support = document.addObject("Part::Feature", "Support")
    support.Label = "Sketch Support"
    support.Shape = Part.makeBox(30, 20, 4)

    sketch = document.addObject("Sketcher::SketchObject", "Sketch")
    sketch.Label = "Native Sketch State Profile"
    PartDesign.initializeDesignDefinition(sketch)
    rectangle_indices = sketch.addGeometry(_rectangle(), False)
    construction_index = sketch.addGeometry(
        Part.LineSegment(App.Vector(10, 0, 0), App.Vector(10, 12, 0)),
        True,
    )
    circle_index = sketch.addGeometry(
        Part.Circle(App.Vector(10, 6, 0), App.Vector(0, 0, 1), 2.5),
        False,
    )
    assert tuple(rectangle_indices) == (0, 1, 2, 3)
    assert int(construction_index) == 4
    assert int(circle_index) == 5

    constraint_indices = sketch.addConstraint(
        [
            Sketcher.Constraint("Horizontal", 0),
            Sketcher.Constraint("Vertical", 1),
            Sketcher.Constraint("Horizontal", 2),
            Sketcher.Constraint("Vertical", 3),
            Sketcher.Constraint("Coincident", 0, 2, 1, 1),
            Sketcher.Constraint("Coincident", 1, 2, 2, 1),
            Sketcher.Constraint("Coincident", 2, 2, 3, 1),
            Sketcher.Constraint("Coincident", 3, 2, 0, 1),
            Sketcher.Constraint("Distance", 0, 20.0),
            Sketcher.Constraint("Radius", 5, 2.5),
        ]
    )
    assert len(tuple(constraint_indices)) == 10
    sketch.renameConstraint(8, "Base width")
    sketch.renameConstraint(9, "Center hole radius")

    sketch.AttachmentSupport = (support, ["Face1"])
    sketch.MapMode = "FlatFace"
    sketch.AttachmentOffset = App.Placement(
        App.Vector(1, 2, 0),
        App.Rotation(App.Vector(0, 0, 1), 5),
    )
    sketch.addExternal(support.Name, "Edge1")
    document.recompute()
    assert sketch.isValid()
    return sketch, support


def _native_state(document) -> dict:
    return {
        "document_uid": str(document.Uid),
        "structural_revision": 11,
        "recent_receipts": [],
    }


def _snapshot(document, controller) -> dict:
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "sketch.edit"
    result = build_active_snapshot(
        document,
        surface.surface_id,
        _native_state(document),
        selection={"document_uid": str(document.Uid), "items": []},
    )
    assert len(json.dumps(result, separators=(",", ":")).encode()) <= (
        MAX_NATIVE_SNAPSHOT_BYTES
    )
    return result


def _assert_exact_state(snapshot: dict, sketch, support) -> None:
    domain = snapshot["domain"]
    assert domain["kind"] == "sketch"
    assert domain["context"] == "edit"
    active = domain["active_sketch"]
    assert active["object_name"] == sketch.Name
    assert active["geometry_count"] == 6
    assert active["constraint_count"] == 10
    assert active["construction_geometry_count"] == 1
    assert active["geometry_truncated"] is False
    assert active["constraints_truncated"] is False
    assert [item["index"] for item in active["geometry"]] == list(range(6))
    assert [item["kind"] for item in active["geometry"]] == [
        "line",
        "line",
        "line",
        "line",
        "line",
        "circle",
    ]
    assert active["geometry"][4]["construction"] is True
    assert active["geometry"][0]["start_mm"] == [0.0, 0.0, 0.0]
    assert active["geometry"][0]["end_mm"] == [20.0, 0.0, 0.0]
    assert active["geometry"][5]["radius_mm"] == 2.5

    width = active["constraints"][8]
    assert width["type"] == "Distance"
    assert width["value"] == 20.0
    assert width["name"] == "Base width"
    assert width["references"][0]["geometry_index"] == 0
    coincident = active["constraints"][4]
    assert coincident["type"] == "Coincident"
    assert coincident["references"] == [
        {"slot": 1, "geometry_index": 0, "position": 2},
        {"slot": 2, "geometry_index": 1, "position": 1},
    ]

    assert active["external_reference_count"] == 1
    external = active["external_references"][0]
    assert external["object"]["object_name"] == support.Name
    assert external["subelement"] == "Edge1"
    assert external["kind"] == "projection"
    assert external["geometry_indices"] == [-3]

    attachment = active["attachment"]
    assert attachment["map_mode"] == "FlatFace"
    assert attachment["support"][0]["object"]["object_name"] == support.Name
    assert attachment["support"][0]["subelements"] == ["Face1"]
    assert attachment["offset"]["origin_mm"] == [1.0, 2.0, 0.0]
    assert len(attachment["offset"]["rotation_xyzw"]) == 4

    profile = active["profile"]
    assert profile["wire_count"] == 2
    assert profile["closed_wire_count"] == 2
    assert profile["open_wire_count"] == 0
    assert profile["closed_profile"] is True
    assert profile["face_maker_succeeded"] is True
    placement = sketch.getGlobalPlacement()

    def vector(value):
        return [
            0.0 if abs(float(item)) < 1.0e-14 else round(float(item), 12)
            for item in (value.x, value.y, value.z)
        ]

    expected_plane = {
        "space": "global",
        "origin_mm": vector(placement.Base),
        "x_direction": vector(
            placement.Rotation.multVec(App.Vector(1.0, 0.0, 0.0))
        ),
        "y_direction": vector(
            placement.Rotation.multVec(App.Vector(0.0, 1.0, 0.0))
        ),
        "normal": vector(
            placement.Rotation.multVec(App.Vector(0.0, 0.0, 1.0))
        ),
    }
    actual_plane = profile["support_plane"]
    assert actual_plane["space"] == "global"
    for field in ("origin_mm", "x_direction", "y_direction", "normal"):
        assert max(
            abs(float(actual) - float(expected))
            for actual, expected in zip(
                actual_plane[field],
                expected_plane[field],
                strict=True,
            )
        ) < 1.0e-11, (field, actual_plane[field], expected_plane[field])

    solver = active["solver"]
    assert solver["degrees_of_freedom"] == int(sketch.DoF)
    assert solver["fully_constrained"] is bool(sketch.FullyConstrained)
    assert solver["conflicting_constraints"] == list(sketch.ConflictingConstraints)
    assert solver["redundant_constraints"] == list(sketch.RedundantConstraints)
    assert solver["malformed_constraints"] == list(sketch.MalformedConstraints)
    assert solver["valid"] is True


def _read_boundary(document, sketch) -> tuple:
    return (
        int(document.UndoCount),
        int(document.RedoCount),
        int(document.getBookedTransactionID()),
        bool(document.HasPendingTransaction),
        int(sketch.GeometryCount),
        int(sketch.ConstraintCount),
        tuple(geometry.TypeId for geometry in sketch.Geometry),
        tuple(
            (
                constraint.Type,
                int(constraint.First),
                int(constraint.FirstPos),
                int(constraint.Second),
                int(constraint.SecondPos),
                float(constraint.Value),
                str(constraint.Name),
            )
            for constraint in sketch.Constraints
        ),
        tuple(
            (obj.Name, tuple(subelements))
            for obj, subelements in sketch.ExternalGeometry
        ),
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeSketchStateGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        sketch, support = _build_sketch(document)
        document.clearUndos()
        _process_events()

        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        assert read_active_ribbon_surface(controller).surface_id == "model"

        assert Gui.activeDocument().setEdit(sketch.Name)
        _process_events()
        assert Gui.activeWorkbench().name() == "SketcherWorkbench"
        assert read_active_ribbon_surface(controller).surface_id == "sketch.edit"

        provider_surface = resolve_native_provider_surface(
            read_active_ribbon_surface(controller),
            build_native_capability_registry(),
        )
        assert provider_surface.available is True
        assert provider_surface.schemas
        assert provider_surface.missing_action_ids == ()
        assert provider_surface.missing_definition_names == ()
        assert provider_surface.incomplete_definition_names == ()

        before_read = _read_boundary(document, sketch)
        first = _snapshot(document, controller)
        second = _snapshot(document, controller)
        after_read = _read_boundary(document, sketch)
        assert first == second
        assert before_read == after_read
        _assert_exact_state(first, sketch, support)

        Gui.activeDocument().resetEdit()
        _process_events()
        save_path = Path(tempfile.mkdtemp(prefix="vibecad-native-sketch-state-")) / (
            "NativeSketchState.FCStd"
        )
        document.saveAs(str(save_path))
        saved_name = document.Name
        sketch_name = sketch.Name
        support_name = support.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        VibeGui._connect_document_observer()
        _process_events()

        sketch = document.getObject(sketch_name)
        support = document.getObject(support_name)
        assert Gui.activeDocument().setEdit(sketch.Name)
        _process_events()
        reopened_before = _read_boundary(document, sketch)
        reopened = _snapshot(document, controller)
        assert reopened_before == _read_boundary(document, sketch)
        _assert_exact_state(reopened, sketch, support)

        print("VIBECAD_NATIVE_SKETCH_STATE_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if Gui.activeDocument() and Gui.activeDocument().getInEdit():
            Gui.activeDocument().resetEdit()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
